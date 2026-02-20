"""
Common utilities.
Shared functions used across the application.
"""

import re

from loguru import logger

from api.constants import BACKEND_API_ENDPOINT
from api.utils.tunnel import TunnelURLProvider


def get_scheme(url: str) -> str | None:
    """
    Extract scheme from a given URL if present.
    Returns None if not found
    """
    idx = url.find("://")
    if idx == -1:
        return None
    return url[:idx]


def _validate_url(url: str) -> None:
    """
    Validate URL format and raise ValueError for invalid URLs.

    Checks for:
    - Empty or whitespace-only URLs
    - Malformed schemes (single slash, missing colon/slashes)
    - Invalid/unsupported schemes
    - Invalid ports (non-numeric, out of range, empty)
    - Missing hosts
    - Invalid characters in hostname (whitespace)
    """
    # Check for empty or whitespace-only URLs
    if not url or not url.strip():
        raise ValueError(
            f"Invalid BACKEND_API_ENDPOINT: URL cannot be empty or whitespace"
        )

    # Check for malformed schemes (single slash like http:/localhost)
    if re.match(r"^https?:/[^/]", url):
        raise ValueError(f"Invalid BACKEND_API_ENDPOINT: malformed scheme in '{url}'")

    # Check for malformed scheme separators (http// or http:xyz without //)
    if re.match(r"^https?//[^/]", url) or re.match(r"^https?:[^/]", url):
        raise ValueError(
            f"Invalid BACKEND_API_ENDPOINT: malformed scheme separator in '{url}'"
        )

    # Check for invalid/unsupported schemes
    scheme = get_scheme(url)
    if scheme and scheme not in ("http", "https"):
        raise ValueError(
            f"Invalid BACKEND_API_ENDPOINT: unsupported scheme '{scheme}' in '{url}'"
        )

    # Parse URL for further validation
    if scheme:
        # URL has a scheme, extract host part
        host_part = url[len(scheme) + 3 :]  # Skip "scheme://"
    else:
        host_part = url

    # Strip trailing slash for host validation
    host_part = host_part.rstrip("/")

    # Check for missing host
    if not host_part or not host_part.strip():
        raise ValueError(f"Invalid BACKEND_API_ENDPOINT: missing host in '{url}'")

    # Check for invalid characters in hostname (whitespace)
    if re.search(r"\s", host_part):
        raise ValueError(
            f"Invalid BACKEND_API_ENDPOINT: invalid characters in hostname '{url}'"
        )

    # Check for invalid port - look for colon followed by anything
    port_match = re.search(r":([^/]*)$", host_part)
    if port_match:
        port_str = port_match.group(1)
        if not port_str:
            raise ValueError(f"Invalid BACKEND_API_ENDPOINT: empty port in '{url}'")
        # Check if port is numeric
        if not port_str.isdigit():
            raise ValueError(f"Invalid BACKEND_API_ENDPOINT: invalid port in '{url}'")
        port = int(port_str)
        if port < 0 or port > 65535:
            raise ValueError(
                f"Invalid BACKEND_API_ENDPOINT: port out of range in '{url}'"
            )


async def get_backend_endpoints() -> tuple[str, str]:
    """
    Get the backend endpoint URLs for external access (webhooks, callbacks, WebSocket connections).

    Priority:
        1. BACKEND_API_ENDPOINT environment variable (if set and not localhost)
        2. Cloudflared Tunnel URLs (fallback for localhost or missing env var)

    Protocol Handling:
        1. If URL has http:// - returns http:// and ws://
        2. If URL has https:// - returns https:// and wss://
        3. If URL has no protocol - defaults to http:// and ws://

    Returns:
        tuple[str, str]: (backend_endpoint, wss_backend_endpoint)

    Raises:
        ValueError: If no endpoint URL can be determined or URL is invalid
    """

    # If env var is explicitly set (even to empty/whitespace), validate it
    if BACKEND_API_ENDPOINT is not None:
        # Validate - this will raise for empty/whitespace
        _validate_url(BACKEND_API_ENDPOINT)

    if BACKEND_API_ENDPOINT:
        # Handle localhost/127.0.0.1 special case - use tunnel URL if available
        if "localhost" in BACKEND_API_ENDPOINT or "127.0.0.1" in BACKEND_API_ENDPOINT:
            logger.debug(
                f"BACKEND_API_ENDPOINT is local ({BACKEND_API_ENDPOINT}), checking tunnel URL"
            )
            try:
                tunnel_urls = await TunnelURLProvider.get_tunnel_urls()
                if tunnel_urls:
                    logger.debug(
                        f"Tunnel URLs available, using tunnel URLs instead of localhost"
                    )
                    return tunnel_urls
                else:
                    logger.debug(
                        f"Tunnel URLs returned None, proceeding with localhost endpoint"
                    )
            except Exception as e:
                logger.debug(
                    f"No tunnel URLs available ({e}), proceeding with localhost endpoint"
                )

        try:
            # Parse the URL to validate and handle protocol
            scheme = get_scheme(BACKEND_API_ENDPOINT)

            if scheme:
                http_url = BACKEND_API_ENDPOINT.rstrip("/")
                ws_scheme = {"http": "ws", "https": "wss"}[scheme]
                ws_url = BACKEND_API_ENDPOINT.rstrip("/").replace(scheme, ws_scheme, 1)
            else:
                http_url = "http://" + BACKEND_API_ENDPOINT.rstrip("/")
                ws_url = "ws://" + BACKEND_API_ENDPOINT.rstrip("/")

            logger.debug(
                f"Returning backend URLs - HTTP: {http_url}, WebSocket: {ws_url}"
            )
            return http_url, ws_url

        except Exception as e:
            # Case 4: Invalid URL format
            raise ValueError(
                f"Invalid BACKEND_API_ENDPOINT format: '{BACKEND_API_ENDPOINT}' - {str(e)}"
            )

    # Second priority: Query cloudflared tunnel URL when no environment variable is set
    logger.debug("No BACKEND_API_ENDPOINT set, using tunnel URL")
    tunnel_urls = await TunnelURLProvider.get_tunnel_urls()
    if tunnel_urls:
        logger.debug(f"Retrieved tunnel URLs: {tunnel_urls}")
        return tunnel_urls
    else:
        logger.debug("No tunnel URLs available")
        raise ValueError(
            "No tunnel URL available. Please set BACKEND_API_ENDPOINT environment "
            "variable or ensure cloudflared service is running."
        )
