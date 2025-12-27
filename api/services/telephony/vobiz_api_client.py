"""
Vobiz API client for call and stream management.
"""

import aiohttp
from typing import Dict, Any, Optional


class VobizApiClient:
    """Centralized client for Vobiz API operations."""
    
    def __init__(self, auth_id: str, auth_token: str):
        """Initialize the Vobiz API client.
        
        Args:
            auth_id: Vobiz Account ID
            auth_token: Vobiz Auth Token
        """
        self.auth_id = auth_id
        self.auth_token = auth_token
        self.base_url = "https://api.vobiz.ai/api"
        
    @property
    def headers(self) -> Dict[str, str]:
        """Get authentication headers for Vobiz API."""
        return {
            "X-Auth-ID": self.auth_id,
            "X-Auth-Token": self.auth_token,
        }
    
    async def stop_audio_stream(self, call_id: str, stream_id: Optional[str] = None) -> Dict[str, Any]:
        """Stop Vobiz audio stream(s) for a call.
        
        Args:
            call_id: The Vobiz call_uuid
            stream_id: Optional specific stream ID. If teh stream ID is not available, stops all streams for the call.
            
        Returns:
            Dict containing the API response
        """
        if stream_id:
            # Stop specific stream
            endpoint = f"{self.base_url}/v1/Account/{self.auth_id}/Call/{call_id}/Stream/{stream_id}/"
        else:
            # Stop all streams for the call
            endpoint = f"{self.base_url}/v1/Account/{self.auth_id}/Call/{call_id}/Stream/"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(endpoint, headers=self.headers) as response:
                    response_text = await response.text()
                    response_data = {}
                    
                    try:
                        response_data = await response.json() if response_text else {}
                    except:
                        # If JSON parsing fails, include raw text
                        response_data = {"raw_text": response_text}
                    
                    return {
                        "status_code": response.status,
                        "response_body": response_data,
                        "raw_text": response_text
                    }
                        
        except Exception as e:
            return {"exception": str(e)}
    
    async def hangup_call(self, call_id: str) -> Dict[str, Any]:
        """Hang up a Vobiz call.
        
        Args:
            call_id: The Vobiz call_uuid
            
        Returns:
            Dict containing the API response
        """
        endpoint = f"{self.base_url}/v1/Account/{self.auth_id}/Call/{call_id}/"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(endpoint, headers=self.headers) as response:
                    response_text = await response.text()
                    response_data = {}
                    
                    try:
                        response_data = await response.json() if response_text else {}
                    except:
                        # If JSON parsing fails, include raw text
                        response_data = {"raw_text": response_text}
                    
                    return {
                        "status_code": response.status,
                        "response_body": response_data,
                        "raw_text": response_text
                    }
                        
        except Exception as e:
            return {"exception": str(e)}
    
    async def stop_streams_and_hangup(self, call_id: str, stream_id: Optional[str] = None) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """Stop audio streams and hang up call in the same order.
        
        Args:
            call_id: The Vobiz call_uuid
            stream_id: Optional specific stream ID. If None, stops all streams for the call.
            
        Returns:
            Tuple of (stream_result, hangup_result)
        """
        # Step 1: Stop audio streams
        stream_result = await self.stop_audio_stream(call_id, stream_id)
        
        # Step 2: Hang up the call  
        hangup_result = await self.hangup_call(call_id)
        
        return stream_result, hangup_result