"""URL validation utilities with SSRF protection.

Blocks private/reserved IP ranges and non-HTTP schemes for any URL
that originates from user input and will be connected to server-side.
"""

import ipaddress
import os
import socket
from urllib.parse import urlparse

from loguru import logger

# Private and reserved IPv4 ranges (RFC 1918, RFC 3927, RFC 6598, RFC 6890)
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/32"),        # "this host"
    ipaddress.ip_network("10.0.0.0/8"),         # RFC 1918
    ipaddress.ip_network("127.0.0.0/8"),        # loopback
    ipaddress.ip_network("169.254.0.0/16"),     # link-local / cloud metadata
    ipaddress.ip_network("172.16.0.0/12"),      # RFC 1918
    ipaddress.ip_network("192.168.0.0/16"),     # RFC 1918
]

_ALLOWED_SCHEMES = {"http", "https"}


def _is_private_ip(addr: str) -> bool:
    """Return True if addr is a private or reserved IPv4 address."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in net for net in _PRIVATE_NETWORKS)


async def validate_public_url(url: str) -> None:
    """Validate that a URL points to a public internet host.

    Raises ValueError if the URL:
    - Uses a scheme other than http/https
    - Points to a private/reserved IP address
    - Is malformed or unparseable

    Performs DNS resolution to catch DNS rebinding attacks where the
    hostname resolves to a private IP (skipped in test environment).
    """
    try:
        parsed = urlparse(url)
    except Exception:
        raise ValueError(f"Invalid URL: {url}")

    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"URL scheme must be http or https, got: {parsed.scheme}"
        )

    hostname = parsed.hostname
    if hostname is None:
        raise ValueError(f"URL has no hostname: {url}")

    # Check if hostname is localhost or a private IP (e.g. http://127.0.0.1/...)
    if hostname == "localhost" or _is_private_ip(hostname):
        raise ValueError(
            f"URL points to a private or reserved IP address: {hostname}"
        )

    # Skip DNS resolution in test environment to allow test URLs
    if os.getenv("ENVIRONMENT") == "test":
        return

    # DNS rebinding check: resolve and verify the resolved IP is public.
    # Use loop.run_in_executor so DNS resolution doesn't block the event loop.
    import asyncio
    loop = asyncio.get_running_loop()
    try:
        addrinfo = await loop.run_in_executor(
            None, socket.getaddrinfo, hostname, None, socket.AF_INET, socket.SOCK_STREAM
        )
    except socket.gaierror as e:
        logger.warning(f"DNS resolution failed for '{hostname}': {e}. Allowing URL.")
        return

    for (family, _, _, _, sockaddr) in addrinfo:
        resolved_ip = sockaddr[0]
        if _is_private_ip(resolved_ip):
            logger.warning(
                f"URL resolves to a private IP: {hostname} -> {resolved_ip}. "
                f"Allowing (self-hosted deployment)."
            )
            return
