"""Review gates for the private Pipecat contracts used by Dograh's worker.

The behavioral tests in test_pipeline_worker_startup.py prove shutdown and
resource ownership. This fixture separately catches upstream method additions,
removals, signature changes, event changes and changes to lifecycle delegation.
It is intentionally an upgrade review gate, not a claim that a call graph proves
all control-flow semantics. After a failure, review the upstream diff, adapt the
wrapper and behavioral scenarios, then update the reviewed fixture explicitly.
Do not automatically regenerate the fixture as part of an upstream upgrade.
"""

import ast
import difflib
import inspect
import json
import textwrap
from pathlib import Path

import pytest
from pipecat.pipeline.worker import PipelineWorker
from pipecat.workers.base_worker import BaseWorker
from pipecat.workers.runner import WorkerRunner

from api.services.pipecat.pipeline_worker import DograhPipelineWorker

CONTRACT_PATH = (
    Path(__file__).parent / "fixtures/pipecat_worker_lifecycle_contract.json"
)

# Include the runner and bus dispatch: setup can be cancelled before StartFrame
# or activation, and those requests must still reach Dograh's _cancel override.
LIFECYCLE_METHODS = {
    PipelineWorker: (
        "run",
        "cancel",
        "end",
        "stop_when_done",
        "on_bus_message",
        "_setup_within_timeout",
        "_setup",
        "_cleanup",
        "_cancel",
        "_create_tasks",
        "_cancel_tasks",
        "_process_push_queue",
        "_wait_for_pipeline_start",
        "_wait_for_pipeline_end",
        "_wait_for_pipeline_finished",
        "_handle_worker_end",
        "_handle_worker_cancel",
        "_source_push_frame",
        "_sink_push_frame",
        "_handle_unusable_processor",
    ),
    BaseWorker: (
        "attach",
        "start",
        "stop",
        "cleanup",
        "wait",
        "add_workers",
        "accepts_bus_message",
        "on_bus_message",
        "_propagate_end_to_children",
        "_propagate_cancel_to_children",
    ),
    WorkerRunner: (
        "add_workers",
        "run",
        "end",
        "cancel",
        "on_bus_message",
        "_setup_session",
        "_start_worker",
        "_run_worker",
        "_cancel_spawned_workers",
        "_finish_running_workers",
    ),
}


def _source_tree(method):
    return ast.parse(textwrap.dedent(inspect.getsource(method)))


def _calls(method):
    """Keep delegation order and await boundaries, ignoring logging/formatting."""
    tree = _source_tree(method)
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    calls = []
    for node in sorted(
        (node for node in ast.walk(tree) if isinstance(node, ast.Call)),
        key=lambda node: (node.lineno, node.col_offset),
    ):
        target = ast.unparse(node.func)
        if target.startswith(("logger.", "warnings.")) or target == "super":
            continue
        if target.endswith(("_call_event_handler", "_register_event_handler")):
            target += f"({ast.unparse(node.args[0])})"
        if isinstance(parents.get(node), ast.Await):
            target = f"await {target}"
        calls.append(target)
    return calls


def _contract(cls):
    return {
        # Inventory all methods, not just known lifecycle names: a new upstream
        # phase may have a name we could not anticipate in our overrides.
        "declared_methods": sorted(
            name
            for name, member in vars(cls).items()
            if inspect.isfunction(member)
            or isinstance(member, (staticmethod, classmethod))
        ),
        "events": [
            ast.literal_eval(node.args[0])
            for node in ast.walk(_source_tree(cls.__init__))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_register_event_handler"
        ],
        "lifecycle": {
            name: (
                {
                    "signature": str(inspect.signature(getattr(cls, name))),
                    "async": inspect.iscoroutinefunction(getattr(cls, name)),
                    "calls": _calls(getattr(cls, name)),
                }
                if hasattr(cls, name)
                else {"missing": True}
            )
            for name in LIFECYCLE_METHODS[cls]
        },
    }


@pytest.mark.parametrize("cls", LIFECYCLE_METHODS, ids=lambda cls: cls.__name__)
def test_upstream_worker_lifecycle_contract_requires_review(cls):
    expected = json.loads(CONTRACT_PATH.read_text())[cls.__name__]
    actual = _contract(cls)
    diff = "\n".join(
        difflib.unified_diff(
            json.dumps(expected, indent=2).splitlines(),
            json.dumps(actual, indent=2).splitlines(),
            fromfile="reviewed contract",
            tofile="installed Pipecat contract",
            lineterm="",
        )
    )
    assert actual == expected, (
        f"{cls.__name__} lifecycle changed. Review DograhPipelineWorker and AgentWorker, "
        "add or adapt startup/shutdown scenarios, then update the reviewed fixture.\n"
        + diff
    )


@pytest.mark.parametrize("name", ["_cancel", "_setup_within_timeout"])
def test_worker_overrides_keep_upstream_hook_signatures(name):
    upstream = getattr(PipelineWorker, name)
    local = getattr(DograhPipelineWorker, name)
    assert inspect.iscoroutinefunction(upstream) and inspect.iscoroutinefunction(local)
    assert inspect.signature(upstream).parameters == inspect.signature(local).parameters
