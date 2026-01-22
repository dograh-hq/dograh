"""
ARQ task to handle call transfer redirect independently from pipeline.

This task runs in a separate worker process, ensuring the transfer logic
is completely decoupled from the real-time audio pipeline.
"""

import asyncio
import aiohttp
from loguru import logger

from api.utils.common import get_backend_endpoints


async def handle_transfer_redirect(
    ctx,
    original_call_sid: str,
    conference_id: str, 
    transfer_call_sid: str
):
    """
    Handle call transfer redirect in ARQ worker, independent of pipeline.
    
    Following the test bench approach:
    1. Wait for WebSocket closure to complete  
    2. Verify conference state (destination still connected)
    3. Redirect original caller to conference using TwiML endpoint
    4. Handle any failures gracefully
    
    Args:
        original_call_sid: The original caller's Twilio call SID
        conference_id: The conference name to join caller to
        transfer_call_sid: The destination call SID (for verification)
    """
    logger.info("=" * 60)
    logger.info("🚀 ARQ TRANSFER REDIRECT STARTED")
    logger.info(f"   Original Caller SID: {original_call_sid}")
    logger.info(f"   Conference ID: {conference_id}")
    logger.info(f"   Destination Call SID: {transfer_call_sid}")
    logger.info("=" * 60)
    
    try:
        # Step 1: Wait for WebSocket closure to complete (test bench approach)
        logger.info("⏱️  Step 1: Waiting for WebSocket closure to complete...")
        await asyncio.sleep(2.0)  # Test bench uses 1.5s, we use 2s for safety
        logger.info("   WebSocket closure wait completed")
        
        # Step 2: Verify destination is still in conference (test bench approach) 
        logger.info("🔍 Step 2: Verifying destination is still in conference...")
        try:
            # TODO: Add actual Twilio conference verification here
            # For now, assume destination is still connected
            logger.info("   Destination verification completed (assuming connected)")
        except Exception as e:
            logger.warning(f"   Could not verify destination: {e}")
        
        # Step 3: Redirect caller to conference (test bench approach)
        logger.info("📞 Step 3: Redirecting caller to conference...")
        
        success = await _redirect_caller_to_conference(original_call_sid, conference_id)
        
        if success:
            logger.info("✅ TRANSFER REDIRECT SUCCESSFUL!")
            logger.info("   Caller should now be in conference with destination")
        else:
            logger.error("❌ TRANSFER REDIRECT FAILED!")
            
    except Exception as e:
        logger.exception(f"❌ Transfer redirect error: {e}")
    
    logger.info("=" * 60)
    logger.info("🏁 ARQ TRANSFER REDIRECT COMPLETED")
    logger.info("=" * 60)


async def _redirect_caller_to_conference(call_sid: str, conference_name: str) -> bool:
    """
    Redirect caller to conference using Twilio API.
    
    Exactly following the test bench approach.
    
    Args:
        call_sid: Twilio call SID to redirect
        conference_name: Name of the conference to join
        
    Returns:
        bool: True if redirect was successful, False otherwise
    """
    logger.info(f"[TRANSFER-DEBUG] _redirect_caller_to_conference called with: {call_sid} and {conference_name}")
    
    # TODO: Use provider service in production instead of hardcoded credentials
    account_sid = ""
    auth_token = ""
    
    try:
        # Get public backend endpoint for TwiML URL
        backend_endpoint, _ = await get_backend_endpoints()
        
        # Construct TwiML endpoint URL
        transfer_url = f"{backend_endpoint}/api/v1/telephony/transfer-twiml/{conference_name}"
        
        logger.info(f"[TRANSFER-DEBUG] Transfer URL: {transfer_url}")
        
        # Twilio API endpoint for updating calls
        api_endpoint = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls/{call_sid}.json"
        
        # Redirect data - exactly like test bench
        redirect_data = {
            "url": transfer_url,
            "method": "POST"
        }
        
        logger.info(f"[TRANSFER-DEBUG] Redirecting caller {call_sid} to conference {conference_name}")
        logger.info(f"[TRANSFER-DEBUG] API endpoint: {api_endpoint}")
        logger.info(f"[TRANSFER-DEBUG] Redirect data: {redirect_data}")
        
        # Make the redirect API call
        async with aiohttp.ClientSession() as session:
            logger.info(f"[TRANSFER-DEBUG] Created aiohttp session")
            auth = aiohttp.BasicAuth(account_sid, auth_token)
            logger.info(f"[TRANSFER-DEBUG] Making POST request to Twilio API for redirect")
            
            async with session.post(api_endpoint, data=redirect_data, auth=auth) as response:
                logger.info(f"[TRANSFER-DEBUG] Received response from Twilio API")
                
                if response.status == 200:
                    logger.info(f"[TRANSFER-DEBUG] API response status: 200") 
                    logger.info(f"[TRANSFER-DEBUG] Successfully redirected caller to conference {conference_name}")
                    return True
                else:
                    error_text = await response.text()
                    logger.error(f"[TRANSFER-DEBUG] Redirect failed - Status: {response.status}, Response: {error_text}")
                    return False
                    
    except Exception as e:
        logger.exception(f"[TRANSFER-DEBUG] Exception during redirect: {e}")
        return False