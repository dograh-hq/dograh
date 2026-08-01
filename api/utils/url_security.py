import ipaddress
import socket
import ssl
from typing import Any
from urllib.parse import urlparse

import httpcore
import httpx

from api.constants import DEPLOYMENT_MODE

_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def validate_user_configured_service_url(
    url: str,
    *,
    field_name: str,
) -> str | None:
    """Restrict user-configured service URLs in hosted deployments.

    OSS deployments commonly point model services at localhost or private LAN
    hosts. SaaS deployments must not allow users to make Dograh infrastructure
    connect to private/internal network locations.

    Returns:
        The validated public IP address string in SaaS mode (for DNS pinning),
        or None in OSS mode.
    """
    if DEPLOYMENT_MODE == "oss":
        return None

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.hostname:
        raise ValueError(f"{field_name} must be an http, https, ws, or wss URL")

    hostname = parsed.hostname
    if hostname.lower() == "localhost":
        raise ValueError(f"{field_name} cannot point to localhost in SaaS mode")

    ips = _resolve_hostname_ips(hostname, parsed.port)
    for ip in ips:
        if _is_blocked_saas_service_ip(ip):
            raise ValueError(
                f"{field_name} must resolve to a public IP address in SaaS mode"
            )
    return str(ips[0]) if ips else None


class _PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Network backend that connects to a pinned IP address to prevent DNS rebinding."""

    def __init__(self, pinned_ip: str) -> None:
        self._pinned_ip = pinned_ip
        self._backend = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: Any = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        return await self._backend.connect_tcp(
            self._pinned_ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        return await self._backend.connect_unix_socket(
            path, timeout=timeout, socket_options=socket_options
        )

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """HTTPX Transport that pins connections to a validated IP address while preserving SNI."""

    def __init__(self, pinned_ip: str, **kwargs: Any) -> None:
        # Build the ssl_context from kwargs the same way httpx.AsyncHTTPTransport
        # does, then hand it straight to the pinned pool so we do not call
        # super().__init__() only to discard the pool it creates.
        verify = kwargs.pop("verify", True)
        cert = kwargs.pop("cert", None)
        trust_env = kwargs.pop("trust_env", True)
        
        if isinstance(verify, ssl.SSLContext):
            ssl_context: ssl.SSLContext | None = verify
        else:
            ssl_context = httpx.create_ssl_context(
                verify=verify, cert=cert, trust_env=trust_env
            )

        http2 = kwargs.pop("http2", False)
        limits = kwargs.pop("limits", httpx.Limits())

        if kwargs:
            raise ValueError(
                f"Unsupported kwargs for PinnedAsyncHTTPTransport: {list(kwargs.keys())}"
            )

        super().__init__()
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl_context,
            http2=http2,
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            network_backend=_PinnedNetworkBackend(pinned_ip),
        )


def get_pinned_httpx_transport(
    pinned_ip: str | None, **kwargs: Any
) -> httpx.AsyncHTTPTransport | None:
    """Return an HTTPX transport pinned to the given IP address, or None if no IP pinning is needed."""
    if not pinned_ip:
        return None
    return PinnedAsyncHTTPTransport(pinned_ip, **kwargs)


def _resolve_hostname_ips(
    hostname: str, port: int | None
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return [ipaddress.ip_address(hostname)]
    except ValueError:
        pass

    try:
        addr_infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ValueError("Could not resolve service URL hostname") from e

    return [ipaddress.ip_address(addr_info[4][0]) for addr_info in addr_infos]


def _is_blocked_saas_service_ip(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (ip.version == 4 and ip in _CGNAT_NETWORK)
    )
