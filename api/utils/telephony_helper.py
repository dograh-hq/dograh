"""
Telephony helper utilities.
Common functions used across telephony operations.
"""

from dograh.api.constants import COUNTRY_CODES
from fastapi import Request
from loguru import logger
from starlette.responses import HTMLResponse


def numbers_match(
    incoming_number: str,
    configured_number: str,
    to_country: str = None,
    from_country: str = None,
) -> bool:
    """
    Check if two phone numbers match, handling different formats with country context.

    Args:
        incoming_number: Phone number from webhook
        configured_number: Phone number from organization config
        to_country: ISO country code for the called number (e.g., "US", "IN")
        from_country: ISO country code for the caller (e.g., "IN", "GB")

    Examples:
    - incoming: "+08043071383", configured: "918043071383", to_country="IN" -> True
    - incoming: "+918043071383", configured: "918043071383" -> True
    - incoming: "+19781899185", configured: "+19781899185" -> True
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

    if to_country:
        country_code = get_country_code(to_country)
        if country_code:
            if _test_number_formats_with_country_code(
                incoming_no_plus, configured_no_plus, country_code
            ):
                return True

    # Fallback to caller country if available
    if from_country and from_country != to_country:
        country_code = get_country_code(from_country)
        if country_code:
            if _test_number_formats_with_country_code(
                incoming_no_plus, configured_no_plus, country_code
            ):
                return True

    # Legacy fallback for common country codes (when no country info available)
    if not to_country and not from_country:
        common_codes = ["91", "1", "44"]  # India, US/Canada, UK
        for code in common_codes:
            if _test_number_formats_with_country_code(
                incoming_no_plus, configured_no_plus, code
            ):
                return True

    return False


def _test_number_formats_with_country_code(
    incoming_no_plus: str, configured_no_plus: str, country_code: str
) -> bool:
    """
    Test different phone number format variations with the given country code to find matches.

    This function handles various international phone number formatting scenarios:
    - Numbers with/without country codes
    - Numbers with leading zeros vs country codes
    - Different representations of the same number across formats

    Args:
        incoming_no_plus: Incoming number without + prefix
        configured_no_plus: Configured number without + prefix
        country_code: International dialing code (e.g., "91", "1")

    Returns:
        True if any format variation produces a match
    """
    # Case 1: Incoming has no country code, configured has it
    if f"{country_code}{incoming_no_plus}" == configured_no_plus:
        return True

    # Case 2: Incoming has leading 0, need to replace with country code
    if incoming_no_plus.startswith("0"):
        local_part = incoming_no_plus[1:]  # Remove leading 0
        if f"{country_code}{local_part}" == configured_no_plus:
            return True

    # Case 3: Configured has no country code, incoming has it
    if f"{country_code}{configured_no_plus}" == incoming_no_plus:
        return True

    # Case 4: Configured has leading 0, need to replace with country code
    if configured_no_plus.startswith("0"):
        local_part = configured_no_plus[1:]  # Remove leading 0
        if f"{country_code}{local_part}" == incoming_no_plus:
            return True

    return False


def normalize_phone_number(phone_number: str, country_code: str = None) -> str:
    """
    Normalize a phone number to E.164 format using country context.

    Args:
        phone_number: Phone number to normalize
        country_code: ISO country code (e.g., "US", "IN") for context

    Returns:
        Phone number in E.164 format (e.g., "+14155552671", "+919876543210")
    """
    if not phone_number:
        return ""

    # Remove spaces, hyphens, and other formatting
    clean_number = (
        phone_number.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    )

    # Already in E.164 format
    if clean_number.startswith("+"):
        return clean_number

    # Get dialing code for the country
    if country_code:
        dialing_code = get_country_code(country_code)
        if dialing_code:
            # Remove leading 0 if present (common in many countries)
            if clean_number.startswith("0"):
                clean_number = clean_number[1:]

            # Add country code if not already present
            if not clean_number.startswith(dialing_code):
                return f"+{dialing_code}{clean_number}"
            else:
                return f"+{clean_number}"

    # Fallback: try to guess common formats
    if clean_number.startswith("0") and len(clean_number) == 11:
        # Without country context, prefer India for now
        return f"+91{clean_number[1:]}"
    elif len(clean_number) == 10:
        # Without context, this is ambiguous - return as-is with + prefix
        return f"+{clean_number}"
    elif not clean_number.startswith("+"):
        # Add + prefix if missing
        return f"+{clean_number}"

    return clean_number


def normalize_webhook_data(provider_class, webhook_data):
    """Normalize webhook data using the provider's parse method"""
    return provider_class.parse_inbound_webhook(webhook_data)


def generic_hangup_response():
    """Return a generic hangup response for unknown/error cases"""
    return HTMLResponse(
        content="<Response><Hangup/></Response>", media_type="application/xml"
    )


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


def get_country_code(country_iso: str) -> str:
    """
    Get the international dialing code for a country.

    Args:
        country_iso: ISO 3166-1 alpha-2 country code (e.g., "US", "IN", "GB")

    Returns:
        International dialing code (e.g., "1", "91", "44") or empty string if not found
    """
    if not country_iso:
        return ""

    return COUNTRY_CODES.get(country_iso.upper(), "")


def get_countries_for_code(dialing_code: str) -> list[str]:
    """
    Get all countries that use a specific dialing code.

    Args:
        dialing_code: International dialing code (e.g., "1", "91")

    Returns:
        List of ISO country codes that use this dialing code
    """
    if not dialing_code:
        return []

    return [country for country, code in COUNTRY_CODES.items() if code == dialing_code]
