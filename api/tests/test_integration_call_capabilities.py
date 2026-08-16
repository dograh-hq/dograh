"""Framework tests for integration call capabilities.

``IntegrationCallCapabilities`` lets an integration contribute *to* a call —
variables resolved before the first turn, and text appended to every node's
system prompt — where the existing hooks only observe a call and deliver data
afterwards.

The property these tests exist to protect is that the contribution is always
optional. An integration that is slow, raises, returns nonsense, or is not
implemented at all must cost the caller nothing but the enrichment itself: the
call still connects, the prompt is still composed, and the first turn still
fires. Anything less would make a third-party package able to break calls.
"""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest

from api.services.integrations import registry
from api.services.integrations.base import (
    IntegrationCallCapabilities,
    IntegrationPackageSpec,
    IntegrationRuntimeContext,
)
from api.services.pipecat.event_handlers import _resolve_pre_call_task
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.pipecat_engine_context_composer import (
    RECORDING_RESPONSE_MODE_INSTRUCTIONS,
    compose_system_prompt_for_node,
)

# ─────────────────────────── registry collection ───────────────────────────


def _package(name: str, factory) -> IntegrationPackageSpec:
    return IntegrationPackageSpec(name=name, create_call_capabilities=factory)


def _runtime_context() -> IntegrationRuntimeContext:
    """A minimal real context.

    The factories under test ignore it, but the registry's failure path reads
    ``workflow_run_id`` off it to attribute the error — so a ``None`` stand-in
    would turn a broken integration into a crash the tests never see.
    """
    return IntegrationRuntimeContext(
        workflow_run_id=1,
        workflow_run=None,
        workflow_graph=None,
        run_definition=None,
        user_config=None,
        is_realtime=False,
        context_messages_provider=list,
    )


@pytest.fixture
def fake_packages(monkeypatch):
    """Swap the real package registry for whatever a test declares."""

    def install(*packages: IntegrationPackageSpec):
        monkeypatch.setattr(registry, "_ensure_loaded", lambda: None)
        monkeypatch.setattr(registry, "all_packages", lambda: list(packages))

    return install


def test_collects_capabilities_from_packages_that_implement_the_hook(fake_packages):
    capability = IntegrationCallCapabilities(name="memory")
    fake_packages(_package("memory", lambda _ctx: capability))

    assert registry.create_call_capabilities(context=_runtime_context()) == [capability]


def test_packages_without_the_hook_are_skipped(fake_packages):
    """The hook is opt-in: existing export-only integrations are unaffected."""
    capability = IntegrationCallCapabilities(name="memory")
    fake_packages(
        IntegrationPackageSpec(name="tuner"),  # no create_call_capabilities
        _package("memory", lambda _ctx: capability),
    )

    assert registry.create_call_capabilities(context=_runtime_context()) == [capability]


def test_factory_returning_none_contributes_nothing(fake_packages):
    """An integration that is installed but disabled for this call opts out."""
    fake_packages(_package("memory", lambda _ctx: None))

    assert registry.create_call_capabilities(context=_runtime_context()) == []


def test_a_raising_factory_cannot_abort_call_setup(fake_packages):
    """One broken integration must not stop the others, or the call."""

    def explode(_ctx):
        raise RuntimeError("integration is misconfigured")

    healthy = IntegrationCallCapabilities(name="healthy")
    fake_packages(
        _package("broken", explode),
        _package("healthy", lambda _ctx: healthy),
    )

    assert registry.create_call_capabilities(context=_runtime_context()) == [healthy]


def test_no_packages_at_all_is_not_an_error(fake_packages):
    fake_packages()

    assert registry.create_call_capabilities(context=_runtime_context()) == []


@pytest.mark.parametrize(
    "junk",
    [
        {"name": "memory"},  # right shape, wrong type
        "memory",
        42,
        object(),
    ],
    ids=["dict", "str", "int", "object"],
)
def test_a_factory_returning_the_wrong_type_is_skipped(fake_packages, junk):
    """Returning nonsense must be caught here, not on a later attribute access.

    Guarding only the call would let a wrong-typed return through to
    run_pipeline and pipecat_engine, where reading .run_pre_call or
    .prompt_addendum off it raises far from anything that can recover.
    """
    healthy = IntegrationCallCapabilities(name="healthy")
    fake_packages(
        _package("junk", lambda _ctx: junk),
        _package("healthy", lambda _ctx: healthy),
    )

    assert registry.create_call_capabilities(context=_runtime_context()) == [healthy]


def test_an_async_factory_mistake_is_skipped_not_stored(fake_packages):
    """An `async def` factory returns a coroutine — caught, not stored.

    The likeliest way to hit the wrong-type path in practice.
    """

    async def accidentally_async(_ctx):
        return IntegrationCallCapabilities(name="memory")

    coroutine = accidentally_async(None)
    try:
        fake_packages(_package("memory", lambda _ctx: coroutine))

        assert registry.create_call_capabilities(context=_runtime_context()) == []
    finally:
        coroutine.close()


# ──────────────────────────── prompt composition ────────────────────────────


class _Node:
    def __init__(self, prompt: str, add_global_prompt: bool = False):
        self.prompt = prompt
        self.add_global_prompt = add_global_prompt
        self.out_edges = []
        self.document_uuids = None


class _Workflow:
    global_node_id = None
    nodes: ClassVar[dict] = {}


def _compose(**kwargs) -> str:
    return compose_system_prompt_for_node(
        node=_Node("Node prompt."),
        workflow=_Workflow(),
        format_prompt=lambda text: text,
        has_recordings=False,
        **kwargs,
    )


def test_prompt_is_unchanged_when_no_integration_contributes():
    """The default must be byte-identical to the pre-hook behaviour."""
    assert _compose() == "Node prompt."


def test_addendum_is_appended_after_the_node_prompt():
    composed = _compose(integration_addenda=["## CALLER MEMORY\nRepeat caller."])

    assert composed == "Node prompt.\n\n## CALLER MEMORY\nRepeat caller."


def test_multiple_addenda_are_appended_in_order():
    composed = _compose(integration_addenda=["First.", "Second."])

    assert composed == "Node prompt.\n\nFirst.\n\nSecond."


def test_empty_addenda_do_not_introduce_blank_sections():
    """A blank contribution must not leave stray separators in the prompt."""
    composed = _compose(integration_addenda=["", None, "Real."])

    assert composed == "Node prompt.\n\nReal."


def test_addendum_follows_the_recording_instructions():
    """Recording instructions are engine-critical, so they keep their position."""
    composed = compose_system_prompt_for_node(
        node=_Node("Say RECORDING_ID: abc"),
        workflow=_Workflow(),
        format_prompt=lambda text: text,
        has_recordings=True,
        integration_addenda=["## CALLER MEMORY"],
    )

    assert composed.index(RECORDING_RESPONSE_MODE_INSTRUCTIONS) < composed.index(
        "## CALLER MEMORY"
    )


# ─────────────────── engine-side addendum resolution ───────────────────


def _engine_with(capabilities, call_context_vars=None) -> PipecatEngine:
    """A bare engine carrying only what addendum resolution reads.

    Built without __init__ deliberately: constructing a real engine needs the
    whole pipeline, and this logic depends on exactly two attributes.
    """
    engine = PipecatEngine.__new__(PipecatEngine)
    engine._integration_capabilities = capabilities
    engine._call_context_vars = call_context_vars or {}
    return engine


def test_addendum_receives_the_call_context_variables():
    seen: dict = {}

    def addendum(context_vars):
        seen.update(context_vars)
        return "block"

    engine = _engine_with(
        [IntegrationCallCapabilities(name="memory", prompt_addendum=addendum)],
        {"caller_number": "+15551234567"},
    )

    assert engine._resolve_integration_addenda() == ["block"]
    assert seen == {"caller_number": "+15551234567"}


def test_capability_without_an_addendum_is_skipped():
    engine = _engine_with([IntegrationCallCapabilities(name="pre-call-only")])

    assert engine._resolve_integration_addenda() == []


def test_addendum_returning_none_or_blank_contributes_nothing():
    engine = _engine_with(
        [
            IntegrationCallCapabilities(name="none", prompt_addendum=lambda _v: None),
            IntegrationCallCapabilities(name="blank", prompt_addendum=lambda _v: "   "),
        ]
    )

    assert engine._resolve_integration_addenda() == []


def test_addendum_text_is_stripped():
    engine = _engine_with(
        [IntegrationCallCapabilities(name="m", prompt_addendum=lambda _v: "  block  ")]
    )

    assert engine._resolve_integration_addenda() == ["block"]


@pytest.mark.parametrize(
    "junk",
    [{"text": "block"}, 42, ["block"], object()],
    ids=["dict", "int", "list", "object"],
)
def test_addendum_returning_a_non_string_is_skipped(junk):
    """Wrong-typed text must not reach .strip().

    That call sits outside the try above, so an AttributeError there would
    escape _resolve_integration_addenda and leave the node with no prompt.
    """
    engine = _engine_with(
        [
            IntegrationCallCapabilities(name="junk", prompt_addendum=lambda _v: junk),
            IntegrationCallCapabilities(name="ok", prompt_addendum=lambda _v: "kept"),
        ]
    )

    assert engine._resolve_integration_addenda() == ["kept"]


def test_a_raising_addendum_does_not_cost_the_caller_a_system_prompt():
    def explode(_vars):
        raise RuntimeError("template blew up")

    engine = _engine_with(
        [
            IntegrationCallCapabilities(name="broken", prompt_addendum=explode),
            IntegrationCallCapabilities(name="ok", prompt_addendum=lambda _v: "kept"),
        ]
    )

    assert engine._resolve_integration_addenda() == ["kept"]


# ──────────────────────── pre-call task resolution ────────────────────────


async def test_completed_task_result_is_returned():
    async def hook():
        return {"memory_context": "Repeat caller."}

    task = asyncio.create_task(hook())
    await task

    assert _resolve_pre_call_task("memory", task) == {
        "memory_context": "Repeat caller."
    }


async def test_still_running_task_is_cancelled_and_yields_nothing():
    """A hook past the budget must not hold the caller on the ringer."""

    async def slow_hook():
        await asyncio.sleep(30)
        return {"never": "arrives"}

    task = asyncio.create_task(slow_hook())
    await asyncio.sleep(0)  # let it start

    assert _resolve_pre_call_task("memory", task) == {}
    assert task.cancelled() or task.cancelling()


async def test_raising_task_yields_nothing():
    async def failing_hook():
        raise RuntimeError("memory service is down")

    task = asyncio.create_task(failing_hook())
    await asyncio.gather(task, return_exceptions=True)

    assert _resolve_pre_call_task("memory", task) == {}


async def test_cancelled_task_yields_nothing():
    async def hook():
        await asyncio.sleep(30)

    task = asyncio.create_task(hook())
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert _resolve_pre_call_task("memory", task) == {}


@pytest.mark.parametrize("bad_result", ["a string", 42, ["a", "list"]])
async def test_non_dict_results_are_ignored(bad_result):
    """initial_context is a mapping; anything else would corrupt the merge."""

    async def hook():
        return bad_result

    task = asyncio.create_task(hook())
    await task

    assert _resolve_pre_call_task("memory", task) == {}


async def test_none_result_is_ignored_quietly():
    """Returning nothing is a normal way to say 'no variables this call'."""

    async def hook():
        return None

    task = asyncio.create_task(hook())
    await task

    assert _resolve_pre_call_task("memory", task) == {}
