"""Turn context management for logging across async boundaries.

This module provides a mechanism to track turn numbers across different
async contexts, working around the limitation that contextvars don't
propagate through asyncio.create_task() calls.
"""

import asyncio
from typing import Dict, Optional

from pipecat.utils.run_context import turn_var


class TurnContextManager:
    """Manages turn context across async task boundaries.

    This class provides a workaround for the issue where contextvars
    don't propagate through asyncio.create_task() calls in the pipecat
    library's event system.
    """

    def __init__(self):
        # Map from task to turn number
        self._task_turns: Dict[asyncio.Task, int] = {}
        # Store the pipeline task reference
        self._pipeline_task: Optional[asyncio.Task] = None
        self._current_turn: int = 0

    def set_pipeline_task(self, task: asyncio.Task):
        """Set the main pipeline task reference."""
        self._pipeline_task = task

    def set_turn(self, turn_number: int):
        """Set the turn number for the current context."""
        self._current_turn = turn_number
        # Set in contextvar for direct access
        turn_var.set(turn_number)

        # Also store for the current task
        try:
            current_task = asyncio.current_task()
            if current_task:
                self._task_turns[current_task] = turn_number
        except RuntimeError:
            pass

    def get_turn(self) -> int:
        """Get the turn number, trying multiple sources."""
        # First try contextvar
        turn = turn_var.get()
        if turn > 0:
            return turn

        # Try current task mapping
        try:
            current_task = asyncio.current_task()
            if current_task and current_task in self._task_turns:
                return self._task_turns[current_task]
        except RuntimeError:
            pass

        # Fall back to stored current turn
        return self._current_turn

    def cleanup_task(self, task: asyncio.Task):
        """Clean up turn mapping for completed tasks."""
        self._task_turns.pop(task, None)


# Global instance
_turn_context_manager = TurnContextManager()


def get_turn_context_manager() -> TurnContextManager:
    """Get the global turn context manager instance."""
    return _turn_context_manager
