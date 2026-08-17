"""Demote one known third-party teardown race; report every other escape as-is.

asyncio surfaces exceptions that escape tasks and timer callbacks through the
loop's exception handler, long after the failing call returned — there is no
stack left to catch them on.

One such report is expected: ``aioice.ice.Connection.close()`` closes its
datagram transports without cancelling the connectivity checks still in flight,
so a STUN retransmit timer scheduled by ``aioice.stun.Transaction`` can fire
against a transport whose ``_sock`` and ``_loop`` are already ``None``. The
resulting ``AttributeError`` is raised inside asyncio's own error-reporting
path, and the timer handle is private to the ``Transaction``, so there is no
seam at which application code could await or cancel it. This is an open
upstream bug (aiortc/aiortc#85), not intended behaviour — it is demoted here
rather than fixed because the fix belongs in aioice.

The demotion is deliberately narrow: only that race's own shapes qualify. Any
other exception escaping aiortc/aioice keeps asyncio's default ERROR reporting,
so a genuinely new failure in those libraries still reaches the operator.

Matching is structural throughout — the failing task's coroutine, the timer
callback's bound instance, and the exception's own traceback — never the
rendered message.
"""

import asyncio

from loguru import logger

_THIRD_PARTY_PACKAGES = ("aiortc", "aioice")
# asyncio nulls the transport's socket and loop on close; the race is only
# benign when the failure comes off that closed transport.
_ASYNCIO_TRANSPORT_MODULE = "/asyncio/selector_events.py"
_INVALID_STATE_MODULES = ("asyncio.exceptions", "aiortc.exceptions")


def _escaped_from_third_party(context: dict) -> str | None:
    """Name the unreachable package the failure escaped from, if any."""

    task = context.get("task") or context.get("future")
    get_coro = getattr(task, "get_coro", None)
    if get_coro is not None:
        code = getattr(get_coro(), "cr_code", None)
        filename = getattr(code, "co_filename", "").replace("\\", "/")
        for package in _THIRD_PARTY_PACKAGES:
            if f"/{package}/" in filename:
                return package

    callback = getattr(context.get("handle"), "_callback", None)
    owner = getattr(callback, "__self__", None)
    module = type(owner).__module__ if owner is not None else ""
    for package in _THIRD_PARTY_PACKAGES:
        if module == package or module.startswith(f"{package}."):
            return package

    return None


def _raised_from_closed_transport(exc: BaseException) -> bool:
    """Whether the traceback passes through asyncio's transport send path."""

    traceback = exc.__traceback__
    while traceback is not None:
        filename = traceback.tb_frame.f_code.co_filename.replace("\\", "/")
        if filename.endswith(_ASYNCIO_TRANSPORT_MODULE):
            return True
        traceback = traceback.tb_next
    return False


def _is_known_teardown_race(context: dict) -> bool:
    """Whether this report is the close-vs-retransmit race rather than a new fault."""

    exc = context.get("exception")

    if exc is None:
        # "Task was destroyed but it is pending!" — a connectivity check left
        # awaiting a STUN future that the failed retransmit never resolved.
        # The same race, observed from the GC side instead of the timer.
        task = context.get("task") or context.get("future")
        done = getattr(task, "done", None)
        return done is not None and not done()

    if type(exc).__name__ == "InvalidStateError" and (
        type(exc).__module__ in _INVALID_STATE_MODULES
    ):
        return True

    # The same AttributeError type raised anywhere else in aioice would be a
    # real defect, so the closed-transport frames are what make it benign.
    return isinstance(exc, AttributeError) and _raised_from_closed_transport(exc)


def install() -> None:
    """Install the router on the running loop."""

    loop = asyncio.get_running_loop()

    def _handle(loop: asyncio.AbstractEventLoop, context: dict) -> None:
        package = _escaped_from_third_party(context)
        if package is None or not _is_known_teardown_race(context):
            loop.default_exception_handler(context)
            return
        logger.opt(exception=context.get("exception")).warning(
            f"{context.get('message')} "
            f"(known {package} teardown race, aiortc/aiortc#85)"
        )

    loop.set_exception_handler(_handle)
