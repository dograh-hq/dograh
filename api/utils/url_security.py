def validate_user_configured_service_url(
    url: str,
    *,
    field_name: str,
) -> None:
    """OSS-only deployment: all service URLs are allowed."""
    return
