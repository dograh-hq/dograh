"""Platform settings cache and resolution service.

Maintains an in-memory cache of global platform settings (such as USD_TO_INR_RATE
and GST_PERCENTAGE) configured by Superadmins in the database, allowing zero-latency
synchronous access during payment calculations and configuration endpoints without
blocking database calls or requiring backend restarts.
"""

from typing import Any, Dict, Optional
from loguru import logger

from api.constants import GST_PERCENTAGE, USD_TO_INR_RATE

# In-memory settings cache: {key: value_str}
_SETTINGS_CACHE: Dict[str, str] = {
    "usd_to_inr_rate": str(USD_TO_INR_RATE),
    "gst_percentage": str(GST_PERCENTAGE),
}


async def refresh_platform_settings_cache() -> None:
    """Load or reload all platform settings from the database into memory."""
    from api.db import db_client

    try:
        settings_dict = await db_client.get_all_settings_dict()
        global _SETTINGS_CACHE
        # Start with env defaults, then overlay DB values
        updated = {
            "usd_to_inr_rate": str(USD_TO_INR_RATE),
            "gst_percentage": str(GST_PERCENTAGE),
        }
        updated.update(settings_dict)
        _SETTINGS_CACHE = updated
        logger.info(
            "Refreshed platform settings cache: usd_to_inr_rate={}, gst_percentage={}",
            _SETTINGS_CACHE.get("usd_to_inr_rate"),
            _SETTINGS_CACHE.get("gst_percentage"),
        )
    except Exception as e:
        logger.warning("Failed to refresh platform settings cache: {}", e)


def get_usd_to_inr_rate() -> float:
    """Get the active USD to INR conversion rate."""
    val = _SETTINGS_CACHE.get("usd_to_inr_rate")
    if val:
        try:
            return float(val)
        except (ValueError, TypeError):
            pass
    return USD_TO_INR_RATE


def get_gst_percentage() -> float:
    """Get the active GST percentage."""
    val = _SETTINGS_CACHE.get("gst_percentage")
    if val:
        try:
            return float(val)
        except (ValueError, TypeError):
            pass
    return GST_PERCENTAGE


def get_all_cached_settings() -> Dict[str, Any]:
    """Return all platform settings formatted with appropriate types."""
    return {
        "usd_to_inr_rate": get_usd_to_inr_rate(),
        "gst_percentage": get_gst_percentage(),
    }


async def update_platform_settings(
    usd_to_inr_rate: Optional[float] = None,
    gst_percentage: Optional[float] = None,
) -> Dict[str, Any]:
    """Persist updated platform settings to DB and update the in-memory cache."""
    from api.db import db_client

    if usd_to_inr_rate is not None:
        if usd_to_inr_rate <= 0:
            raise ValueError("usd_to_inr_rate must be greater than 0")
        await db_client.set_setting(
            key="usd_to_inr_rate",
            value=f"{usd_to_inr_rate:.4f}".rstrip("0").rstrip("."),
            description="USD to INR exchange rate for wallet top-ups and Razorpay payments",
        )
        _SETTINGS_CACHE["usd_to_inr_rate"] = str(usd_to_inr_rate)

    if gst_percentage is not None:
        if gst_percentage < 0 or gst_percentage > 100:
            raise ValueError("gst_percentage must be between 0 and 100")
        await db_client.set_setting(
            key="gst_percentage",
            value=f"{gst_percentage:.2f}".rstrip("0").rstrip("."),
            description="Goods and Services Tax (GST) percentage applied to top-up orders",
        )
        _SETTINGS_CACHE["gst_percentage"] = str(gst_percentage)

    return get_all_cached_settings()
