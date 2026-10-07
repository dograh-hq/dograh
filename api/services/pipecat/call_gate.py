"""Answer gating for calls whose media connects before the recipient picks up.

Some providers (WhatsApp today) bring the WebRTC transport up while the handset
is still ringing, so the pipeline must not speak until the call is actually
answered. A bare ``asyncio.Event`` is not enough for that: every terminal
outcome has to release the waiter or the pipeline hangs forever, which means a
call that dies before it is answered wakes the pipeline exactly like a call that
was picked up. The pipeline then starts recording and greets nobody.

``OutboundCallGate`` records *why* it was released before releasing it, so a
waiter that wakes can always tell the two apart without polling or sleeping.
"""

import asyncio
from typing import Optional

ANSWERED = "answered"
TERMINATED = "terminated"


class OutboundCallGate:
    """A one-shot gate carrying the outcome that opened it."""

    __slots__ = ("_event", "_outcome")

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._outcome: Optional[str] = None

    @property
    def outcome(self) -> Optional[str]:
        """``ANSWERED``/``TERMINATED`` once resolved, else None."""
        return self._outcome

    def resolve(self, outcome: str) -> None:
        """Release waiters with ``outcome``. First resolution wins.

        Ordering matters: the outcome is stored before the event is set, so a
        waiter can never observe a released gate with no outcome on it.
        """
        if self._outcome is None:
            self._outcome = outcome
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> str:
        """Block until resolved, returning the outcome.

        Falls back to ``TERMINATED`` for the impossible case of a set event with
        no recorded outcome: not speaking on a live call is recoverable, speaking
        on a dead one is not.
        """
        await self._event.wait()
        return self._outcome or TERMINATED
