import re
from typing import Optional, Tuple

try:
    from fastapi import HTTPException
except ImportError:

    class HTTPException(Exception):  # type: ignore
        def __init__(self, status_code: int = 400, detail: str = ""):
            self.status_code = status_code
            self.detail = detail
            super().__init__(detail)


# Meta officially restricts Business-Initiated Calls (BIC) in:
# United States (+1), Canada (+1), Egypt (+20), Vietnam (+84), Nigeria (+234)
RESTRICTED_BIC_COUNTRIES = {
    "US": "United States",
    "CA": "Canada",
    "EG": "Egypt",
    "VN": "Vietnam",
    "NG": "Nigeria",
}

RESTRICTED_BIC_PREFIXES = {
    "+1": "United States and Canada (+1)",
    "+20": "Egypt (+20)",
    "+84": "Vietnam (+84)",
    "+234": "Nigeria (+234)",
}


_NON_DIAL_CHARS_RE = re.compile(r"[^\d+]")


def _restricted_reason(name: str) -> str:
    return (
        f"Business-Initiated WhatsApp Calls are not permitted in {name} "
        "due to Meta platform regulations. Inbound calling remains supported."
    )


def is_restricted_country(phone_number: str) -> Tuple[bool, Optional[str]]:
    """Check whether a number is in one of Meta's restricted BIC countries.

    Classifies numbers in E.164 format with a leading "+" or digits.
    Returns:
        Tuple of (is_restricted: bool, reason: Optional[str])
    """
    stripped = _NON_DIAL_CHARS_RE.sub("", phone_number.strip())
    if "+" not in stripped:
        return False, None

    digits_only = stripped.replace("+", "")
    if not digits_only:
        # "+" or "++" alone.
        return False, None

    canonical = "+" + digits_only
    for prefix, name in RESTRICTED_BIC_PREFIXES.items():
        if canonical.startswith(prefix):
            return True, _restricted_reason(name)
    return False, None


def validate_destination_country(phone_number: str) -> None:
    """Validate that the destination phone number is valid E.164 and not in Meta's restricted countries.

    Raises:
        HTTPException: 400 if the destination is invalid E.164 or in a restricted country.
    """
    from api.utils.telephony_address import is_e164

    if not is_e164(phone_number):
        raise HTTPException(
            status_code=400,
            detail=(
                "Phone number must be in strict E.164 format, including the country "
                f"code with a leading '+' (e.g. +14155552671). Got: {phone_number!r}"
            ),
        )

    restricted, reason = is_restricted_country(phone_number)
    if restricted:
        raise HTTPException(status_code=400, detail=reason)
