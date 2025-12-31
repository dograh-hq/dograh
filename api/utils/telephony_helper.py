"""
Telephony helper utilities.
Common functions used across telephony operations.
"""

from fastapi import Request
from loguru import logger
from starlette.responses import HTMLResponse


def numbers_match(incoming_number: str, configured_number: str) -> bool: #TODO: check if country code is available in request
    """
    Check if two phone numbers match, handling different formats.
    
    Examples:
    - incoming: "+08043071383", configured: "918043071383" -> True (missing country code)
    - incoming: "+918043071383", configured: "918043071383" -> True (exact match without +)
    - incoming: "+19781899185", configured: "+19781899185" -> True (exact match)
    """
    if not incoming_number or not configured_number:
        return False
    
    # Remove spaces and normalize
    incoming_clean = incoming_number.replace(" ", "").replace("-", "")
    configured_clean = configured_number.replace(" ", "").replace("-", "")
    
    # Direct match
    if incoming_clean == configured_clean:
        return True
    
    # Remove + from both and compare
    incoming_no_plus = incoming_clean.lstrip("+")
    configured_no_plus = configured_clean.lstrip("+")
    
    if incoming_no_plus == configured_no_plus:
        return True
    
    # Handle missing country code in incoming number
    # If incoming has + but configured doesn't, and incoming is missing country code
    if incoming_clean.startswith("+") and not configured_clean.startswith("+"):
        # Try adding common country codes
        if incoming_no_plus.startswith("0") and len(incoming_no_plus) == 11:
            # Might be missing country code 91 (India)
            if f"91{incoming_no_plus[1:]}" == configured_no_plus:
                return True
        elif len(incoming_no_plus) == 10:
            # Could be missing country code
            if f"91{incoming_no_plus}" == configured_no_plus:  # India
                return True
            if f"1{incoming_no_plus}" == configured_no_plus:   # US
                return True
    
    # Handle case where configured has country code but incoming doesn't
    if not incoming_clean.startswith("+") and configured_clean.startswith("+"):
        if f"+{incoming_no_plus}" == configured_clean:
            return True
        # Try common country codes
        if f"+91{incoming_no_plus}" == configured_clean:  # India
            return True
        if f"+1{incoming_no_plus}" == configured_clean:   # US
            return True
    
    return False


def normalize_webhook_data(provider_class, webhook_data):
    """Normalize webhook data using the provider's parse method"""
    return provider_class.parse_inbound_webhook(webhook_data)


def generic_hangup_response():
    """Return a generic hangup response for unknown/error cases"""
    return HTMLResponse(content="<Response><Hangup/></Response>", media_type="application/xml")


async def parse_webhook_request(request: Request) -> tuple[dict, str]:
    """Parse webhook request data from either JSON or form"""
    try:
        # Try JSON first
        webhook_data = await request.json()
        data_source = "JSON"
    except Exception:
        try:
            # Fallback to form data
            form_data = await request.form()
            webhook_data = dict(form_data)
            data_source = "FORM"
        except Exception as e:
            logger.error(f"Failed to parse webhook data: {e}")
            raise ValueError("Unable to parse webhook data")
    
    return webhook_data, data_source