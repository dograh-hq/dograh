import ipaddress
import socket
from urllib.parse import urlparse

from api.constants import DEPLOYMENT_MODE

_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def validate_user_configured_service_url(
    url: str,
    *,
    field_name: str,
) -> None:
    """Restrict user-configured service URLs in hosted deployments.

    OSS deployments commonly point model services at localhost or private LAN
    hosts. SaaS deployments must not allow users to make Dograh infrastructure
    connect to private/internal network locations.
    """
    if DEPLOYMENT_MODE == "oss":
        return

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.hostname:
        raise ValueError(f"{field_name} must be an http, https, ws, or wss URL")

    hostname = parsed.hostname
    if hostname.lower() == "localhost":
        raise ValueError(f"{field_name} cannot point to localhost in SaaS mode")

    for ip in _resolve_hostname_ips(hostname, parsed.port):
        if _is_blocked_saas_service_ip(ip):
            raise ValueError(
                f"{field_name} must resolve to a public IP address in SaaS mode"
            )


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
