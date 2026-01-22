"""Test utilities for transfer coordination.

This module provides utilities to test Redis-based transfer coordination
across multiple instances.
"""

import asyncio
import time
import uuid
from typing import Optional

from loguru import logger

from api.services.telephony.transfer_coordination import get_transfer_coordinator
from api.services.telephony.transfer_event_protocol import (
    TransferContext,
    TransferEvent,
    TransferEventType
)


async def test_redis_coordination():
    """Test basic Redis pub/sub coordination for transfers."""
    logger.info("Testing Redis-based transfer coordination...")
    
    transfer_coordinator = await get_transfer_coordinator()
    
    # Test 1: Store and retrieve transfer context
    tool_call_id = str(uuid.uuid4())
    test_context = TransferContext(
        tool_call_id=tool_call_id,
        call_sid="test_call_123",
        target_number="+1234567890",
        tool_uuid="test_tool_uuid",
        original_call_sid="original_call_123",
        caller_number="+0987654321",
        initiated_at=time.time(),
        workflow_run_id=123
    )
    
    logger.info("Test 1: Storing transfer context...")
    await transfer_coordinator.store_transfer_context(test_context)
    
    logger.info("Test 1: Retrieving transfer context...")
    retrieved_context = await transfer_coordinator.get_transfer_context(tool_call_id)
    
    if retrieved_context and retrieved_context.tool_call_id == tool_call_id:
        logger.info("✅ Test 1 PASSED: Context storage/retrieval works")
    else:
        logger.error("❌ Test 1 FAILED: Context storage/retrieval failed")
        return False
    
    # Test 2: Event publishing and waiting
    logger.info("Test 2: Testing event publishing...")
    
    # Start waiting for completion in background
    async def wait_for_completion():
        return await transfer_coordinator.wait_for_transfer_completion(tool_call_id, 5.0)
    
    wait_task = asyncio.create_task(wait_for_completion())
    
    # Give it a moment to start waiting
    await asyncio.sleep(0.5)
    
    # Publish completion event
    test_event = TransferEvent(
        type=TransferEventType.TRANSFER_COMPLETED,
        tool_call_id=tool_call_id,
        workflow_run_id=123,
        original_call_sid="original_call_123",
        transfer_call_sid="transfer_call_456",
        conference_name="test-conference",
        message="Test transfer completed successfully",
        status="success",
        action="transfer_success"
    )
    
    logger.info("Test 2: Publishing completion event...")
    await transfer_coordinator.publish_transfer_event(test_event)
    
    # Wait for the completion
    received_event = await wait_task
    
    if received_event and received_event.tool_call_id == tool_call_id:
        logger.info("✅ Test 2 PASSED: Event pub/sub works")
    else:
        logger.error("❌ Test 2 FAILED: Event pub/sub failed")
        return False
    
    # Test 3: Cleanup
    logger.info("Test 3: Testing cleanup...")
    await transfer_coordinator.remove_transfer_context(tool_call_id)
    
    cleanup_context = await transfer_coordinator.get_transfer_context(tool_call_id)
    if cleanup_context is None:
        logger.info("✅ Test 3 PASSED: Cleanup works")
    else:
        logger.error("❌ Test 3 FAILED: Cleanup failed")
        return False
    
    logger.info("✅ All tests PASSED! Redis coordination is working correctly.")
    return True


async def test_timeout_handling():
    """Test timeout handling in transfer coordination."""
    logger.info("Testing timeout handling...")
    
    transfer_coordinator = await get_transfer_coordinator()
    tool_call_id = str(uuid.uuid4())
    
    # Wait for completion with short timeout (should timeout)
    start_time = time.time()
    result = await transfer_coordinator.wait_for_transfer_completion(tool_call_id, 2.0)
    elapsed = time.time() - start_time
    
    if result is None and elapsed >= 2.0:
        logger.info("✅ Timeout test PASSED: Properly timed out after 2 seconds")
        return True
    else:
        logger.error(f"❌ Timeout test FAILED: Expected timeout, got {result} in {elapsed}s")
        return False


if __name__ == "__main__":
    async def main():
        success1 = await test_redis_coordination()
        success2 = await test_timeout_handling()
        
        if success1 and success2:
            logger.info("🎉 All transfer coordination tests PASSED!")
        else:
            logger.error("💥 Some tests FAILED!")
    
    asyncio.run(main())