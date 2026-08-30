"""Cloudonix regional SIP edges.

One table drives both the inbound endpoints shown to customers and the remote
peer a Dograh-managed outbound trunk terminates on, so the two cannot drift.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CloudonixRegion:
    """One Cloudonix SIP region.

    ``edge_ip`` is the address customers whitelist for calls arriving from
    Cloudonix, and is also the peer a Dograh-managed outbound trunk sends to.
    """

    name: str
    hostname_suffix: str
    sip_port: int
    tls_port: int
    edge_ip: str

    def hostname(self, domain_uuid: str) -> str:
        return f"{domain_uuid}.{self.hostname_suffix}"


CLOUDONIX_REGIONS: tuple[CloudonixRegion, ...] = (
    CloudonixRegion(
        name="India",
        hostname_suffix="in.dimi.tel",
        sip_port=9060,
        tls_port=9443,
        edge_ip="128.199.27.19",
    ),
    CloudonixRegion(
        name="UAE",
        hostname_suffix="uae.dimi.tel",
        sip_port=9081,
        tls_port=9443,
        edge_ip="20.233.60.70",
    ),
    CloudonixRegion(
        name="Global",
        hostname_suffix="sip.cloudonix.net",
        sip_port=5060,
        tls_port=443,
        edge_ip="18.219.128.166",
    ),
)

DEFAULT_CLOUDONIX_REGION = "Global"

CLOUDONIX_REGION_NAMES: tuple[str, ...] = tuple(
    region.name for region in CLOUDONIX_REGIONS
)


def get_cloudonix_region(name: str | None) -> CloudonixRegion | None:
    """Look a region up by name, case-insensitively. ``None`` when unknown."""
    if not isinstance(name, str):
        return None
    normalized = name.strip().casefold()
    if not normalized:
        return None
    return next(
        (
            region
            for region in CLOUDONIX_REGIONS
            if region.name.casefold() == normalized
        ),
        None,
    )
