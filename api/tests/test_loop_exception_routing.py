"""Escaped-exception routing: only the known aioice teardown race is demoted."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import loguru

from api.services.observability import loop_exceptions

_AIOICE_FILE = "/x/site-packages/aioice/ice.py"
_AIORTC_FILE = "/x/site-packages/aiortc/rtcicetransport.py"
_ASYNCIO_TRANSPORT_FILE = "/x/lib/python3.13/asyncio/selector_events.py"


class _AiortcInvalidStateError(Exception):
    """Stands in for aiortc.exceptions.InvalidStateError."""


_AiortcInvalidStateError.__name__ = "InvalidStateError"
_AiortcInvalidStateError.__qualname__ = "InvalidStateError"
_AiortcInvalidStateError.__module__ = "aiortc.exceptions"


def _coroutine_defined_in(filename: str, body: str = "raise RuntimeError('boom')"):
    """Build a real coroutine whose code object carries the given filename."""
    code = compile(f"async def escaped():\n    {body}", filename, "exec")
    namespace: dict = {"asyncio": asyncio}
    exec(code, namespace)  # noqa: S102
    return namespace["escaped"]()


def _raised_in(filename: str, exc_expression: str) -> Exception:
    """Capture an exception whose traceback passes through the given filename."""
    code = compile(f"def boom():\n    raise {exc_expression}", filename, "exec")
    namespace: dict = {}
    exec(code, namespace)  # noqa: S102
    try:
        namespace["boom"]()
    except Exception as exc:  # noqa: BLE001 - the exception is the fixture
        return exc
    raise AssertionError("expression did not raise")


def _aioice_timer_handle():
    """A TimerHandle whose callback is bound to an aioice.stun.Transaction."""

    class _Transaction:
        def retry(self):
            pass

    _Transaction.__module__ = "aioice.stun"
    return SimpleNamespace(_callback=_Transaction().retry)


async def _finished_task(filename: str, body: str) -> asyncio.Task:
    task = asyncio.ensure_future(_coroutine_defined_in(filename, body))
    await asyncio.gather(task, return_exceptions=True)
    return task


async def _route(context: dict) -> tuple[list, object]:
    """Install the router, feed it one context, report (records, default-mock)."""
    loop = asyncio.get_running_loop()
    loop_exceptions.install()
    handler = loop.get_exception_handler()

    records = []
    handler_id = loguru.logger.add(
        lambda message: records.append(message.record), level="TRACE"
    )
    try:
        with patch.object(loop, "default_exception_handler") as default:
            handler(loop, context)
    finally:
        loguru.logger.remove(handler_id)
        loop.set_exception_handler(None)
    return records, default


# --- the known race is demoted ------------------------------------------------


async def test_stun_retransmit_on_closed_transport_is_demoted():
    """The observed shape: aioice timer, AttributeError off a closed transport."""
    context = {
        "message": "Exception in callback Transaction.__retry()",
        "exception": _raised_in(
            _ASYNCIO_TRANSPORT_FILE,
            "AttributeError(\"'NoneType' object has no attribute 'call_exception_handler'\")",
        ),
        "handle": _aioice_timer_handle(),
    }

    records, default = await _route(context)

    default.assert_not_called()
    assert [r["level"].name for r in records] == ["WARNING"]
    assert "aiortc/aiortc#85" in records[0]["message"]
    assert records[0]["exception"] is not None


async def test_stranded_connectivity_check_is_demoted():
    """'Task was destroyed but it is pending!' carries no exception at all."""
    task = asyncio.ensure_future(
        _coroutine_defined_in(_AIOICE_FILE, "await asyncio.sleep(30)")
    )
    await asyncio.sleep(0)
    try:
        records, default = await _route(
            {"message": "Task was destroyed but it is pending!", "task": task}
        )
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    default.assert_not_called()
    assert [r["level"].name for r in records] == ["WARNING"]


async def test_ice_transport_closed_is_demoted():
    """aiortc reports InvalidStateError from the same teardown window."""
    task = await _finished_task(_AIORTC_FILE, "raise RuntimeError('closed')")

    records, default = await _route(
        {
            "message": "Task exception was never retrieved",
            "exception": _AiortcInvalidStateError("RTCIceTransport is closed"),
            "future": task,
        }
    )

    default.assert_not_called()
    assert [r["level"].name for r in records] == ["WARNING"]


# --- anything else from those packages still reaches the operator -------------


async def test_attribute_error_outside_transport_teardown_stays_error():
    """The same exception type raised inside aioice itself is a real defect."""
    context = {
        "message": "Exception in callback Transaction.__retry()",
        "exception": _raised_in(
            _AIOICE_FILE, "AttributeError('Transaction has no attribute foo')"
        ),
        "handle": _aioice_timer_handle(),
    }

    records, default = await _route(context)

    default.assert_called_once_with(context)
    assert records == []


async def test_novel_aioice_failure_stays_error():
    task = await _finished_task(_AIOICE_FILE, "raise ValueError('unparseable STUN')")
    context = {
        "message": "Task exception was never retrieved",
        "exception": task.exception(),
        "future": task,
    }

    records, default = await _route(context)

    default.assert_called_once_with(context)
    assert records == []


async def test_completed_aioice_task_without_exception_stays_error():
    """A finished task is not the stranded-check shape, so it is not demoted."""
    task = await _finished_task(_AIOICE_FILE, "pass")
    context = {"message": "Unexpected report", "task": task}

    records, default = await _route(context)

    default.assert_called_once_with(context)
    assert records == []


# --- our own failures are never touched ---------------------------------------


async def test_our_task_failures_keep_default_error_reporting():
    task = await _finished_task(
        "/data/dograh/app/api/services/thing.py", "raise RuntimeError('boom')"
    )
    context = {
        "message": "Task exception was never retrieved",
        "exception": task.exception(),
        "future": task,
    }

    records, default = await _route(context)

    default.assert_called_once_with(context)
    assert records == []


async def test_our_closed_transport_failure_keeps_default_error_reporting():
    """The teardown shape alone is not enough — the origin must be third party."""
    context = {
        "message": "Exception in callback Thing.retry()",
        "exception": _raised_in(
            _ASYNCIO_TRANSPORT_FILE,
            "AttributeError(\"'NoneType' object has no attribute 'sendto'\")",
        ),
        "handle": SimpleNamespace(_callback=lambda: None),
    }

    records, default = await _route(context)

    default.assert_called_once_with(context)
    assert records == []


async def test_contexts_without_task_or_handle_keep_default_reporting():
    context = {"message": "Unhandled error in exception handler"}
    records, default = await _route(context)

    default.assert_called_once_with(context)
    assert records == []
