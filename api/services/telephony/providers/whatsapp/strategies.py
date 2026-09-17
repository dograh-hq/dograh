"""WhatsApp transfer and hangup strategies.

This module implements strategies for handling call transfers and hangups
in WhatsApp connections, following the pattern from other telephony providers.
"""

from typing import Any, Dict

from loguru import logger
from pipecat.serializers.call_strategies import HangupStrategy, TransferStrategy


class WhatsAppTransferStrategy(TransferStrategy):
    """WhatsApp transfer strategy (transfers not supported in this release)."""

    async def execute_transfer(self, context: Dict[str, Any]) -> bool:
        """Execute transfer for WhatsApp call (unsupported)."""
        logger.warning("[WhatsApp] Call transfer is not supported in this release")
        return False


class WhatsAppHangupStrategy(HangupStrategy):
    """WhatsApp hangup strategy."""

    async def execute_hangup(self, context: Dict[str, Any]) -> bool:
        """Execute hangup for WhatsApp call."""
        logger.info("[WhatsApp] Call hangup strategy invoked")
        return True
