"""Registry to store WebSocket senders by workflow_run_id.

This allows the pipeline observer to send messages back through
the signaling WebSocket without passing the WebSocket directly.
"""

from typing import Awaitable, Callable, Dict, Optional

_ws_senders: Dict[int, Callable[[dict], Awaitable[None]]] = {}


def register_ws_sender(
    workflow_run_id: int, sender: Callable[[dict], Awaitable[None]]
) -> None:
    """Register a WebSocket sender for a workflow run."""
    _ws_senders[workflow_run_id] = sender


def unregister_ws_sender(workflow_run_id: int) -> None:
    """Unregister a WebSocket sender for a workflow run."""
    _ws_senders.pop(workflow_run_id, None)


def get_ws_sender(
    workflow_run_id: int,
) -> Optional[Callable[[dict], Awaitable[None]]]:
    """Get the WebSocket sender for a workflow run."""
    return _ws_senders.get(workflow_run_id)
