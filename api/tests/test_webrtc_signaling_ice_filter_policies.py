import api.routes.webrtc_signaling as webrtc_signaling
from api.routes.webrtc_signaling import (
    NonRelayFilterPolicy,
    _keep_candidate,
    resolve_ice_filter_policies,
)

_PUBLIC_HOST_CANDIDATE = "candidate:1 1 udp 2122260223 1.1.1.1 45406 typ host"
_PRIVATE_HOST_CANDIDATE = "candidate:1 1 udp 2122260223 192.168.1.10 45406 typ host"
_CGNAT_HOST_CANDIDATE = "candidate:1 1 udp 2122260223 100.64.1.10 45406 typ host"
_RELAY_CANDIDATE = (
    "candidate:1 1 udp 2122260223 100.64.1.10 49188 typ relay raddr 0.0.0.0 rport 0"
)


def test_force_turn_relay_on_cgnat_server_drops_public_inbound_candidates():
    # A CGNAT/tailscale-range SERVER_IP with FORCE_TURN_RELAY=true must still
    # reject a leaked public-IP candidate — relay was explicitly requested
    # because direct/public connectivity is known not to work, so a public
    # inbound candidate can never succeed and should be dropped rather than
    # wasting the ICE timeout on it.
    outbound, inbound = resolve_ice_filter_policies(
        environment="production",
        force_turn_relay=True,
        server_ip="100.64.1.10",
    )
    assert outbound == NonRelayFilterPolicy.ALL
    assert inbound == NonRelayFilterPolicy.PUBLIC


def test_force_turn_relay_on_public_server_drops_private_inbound_candidates():
    outbound, inbound = resolve_ice_filter_policies(
        environment="production",
        force_turn_relay=True,
        server_ip="8.8.8.8",
    )
    assert outbound == NonRelayFilterPolicy.ALL
    assert inbound == NonRelayFilterPolicy.PRIVATE


def test_force_turn_relay_on_plain_private_lan_keeps_prior_unfiltered_behavior():
    # A plain RFC1918 LAN server (not CGNAT) usually still has normal NAT'd
    # internet access, so a public inbound candidate might genuinely work —
    # this must stay NONE exactly as before, unlike the CGNAT case above.
    # Matches the pre-existing
    # test_force_turn_relay_stays_relay_only_on_private_lan in
    # test_is_private_ip_candidate.py, which this must not break.
    outbound, inbound = resolve_ice_filter_policies(
        environment="production",
        force_turn_relay=True,
        server_ip="192.168.50.24",
    )
    assert outbound == NonRelayFilterPolicy.ALL
    assert inbound == NonRelayFilterPolicy.NONE


def test_keep_candidate_public_policy_drops_public_keeps_private():
    assert _keep_candidate(_PUBLIC_HOST_CANDIDATE, NonRelayFilterPolicy.PUBLIC) is False
    assert _keep_candidate(_PRIVATE_HOST_CANDIDATE, NonRelayFilterPolicy.PUBLIC) is True
    assert _keep_candidate(_CGNAT_HOST_CANDIDATE, NonRelayFilterPolicy.PUBLIC) is True


def test_keep_candidate_relay_always_passes_regardless_of_policy():
    for policy in NonRelayFilterPolicy:
        assert _keep_candidate(_RELAY_CANDIDATE, policy) is True


def _urls(servers):
    """Flatten RTCIceServer.urls, which may be a bare str or a list."""
    flat = []
    for server in servers:
        urls = server.urls
        flat.extend(urls if isinstance(urls, list) else [urls])
    return flat


def _stub_turn(monkeypatch, *, host="100.64.1.10"):
    monkeypatch.setattr(webrtc_signaling, "ENABLE_COTURN", True)
    monkeypatch.setattr(webrtc_signaling, "TURN_HOST", host)
    monkeypatch.setattr(webrtc_signaling, "TURN_SECRET", "s3cret")
    monkeypatch.setattr(
        webrtc_signaling,
        "generate_turn_credentials",
        lambda user_id: {
            "uris": [f"turn:{host}:3478"],
            "username": "user",
            "password": "pass",
            "ttl": 86400,
        },
    )


def test_get_ice_servers_omits_public_stun_in_relay_only_mode(monkeypatch):
    # FORCE_TURN_RELAY means "direct connectivity is known not to work here".
    # A public STUN entry can only ever yield a server-reflexive candidate,
    # never a relay one, so it cannot help — it can only gather a public-IP
    # candidate that filter_outbound_sdp() then strips back out. The server
    # must agree with the client, which already skips STUN in this mode.
    monkeypatch.setattr(webrtc_signaling, "FORCE_TURN_RELAY", True)
    _stub_turn(monkeypatch)

    urls = _urls(webrtc_signaling.get_ice_servers(user_id="1"))

    assert not any(url.startswith("stun:") for url in urls)
    assert urls == ["turn:100.64.1.10:3478"]


def test_get_ice_servers_keeps_public_stun_when_not_relay_only(monkeypatch):
    # Unchanged behaviour off the relay-only path.
    monkeypatch.setattr(webrtc_signaling, "FORCE_TURN_RELAY", False)
    monkeypatch.setattr(webrtc_signaling, "ENABLE_COTURN", False)

    assert _urls(webrtc_signaling.get_ice_servers()) == ["stun:stun.l.google.com:19302"]


def test_get_ice_servers_relay_only_without_turn_returns_no_servers(monkeypatch):
    # No silent fallback to STUN when TURN is missing: an empty list is the
    # honest answer (the connection genuinely cannot succeed), and the error
    # logged alongside it is what makes that diagnosable.
    monkeypatch.setattr(webrtc_signaling, "FORCE_TURN_RELAY", True)
    monkeypatch.setattr(webrtc_signaling, "ENABLE_COTURN", False)
    monkeypatch.setattr(webrtc_signaling, "TURN_HOST", "")

    assert webrtc_signaling.get_ice_servers(user_id="1") == []
