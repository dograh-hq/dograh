from api.routes.webrtc_signaling import (
    NonRelayFilterPolicy,
    _keep_candidate,
    resolve_ice_filter_policies,
)

_PUBLIC_HOST_CANDIDATE = (
    "candidate:1 1 udp 2122260223 1.1.1.1 45406 typ host"
)
_PRIVATE_HOST_CANDIDATE = (
    "candidate:1 1 udp 2122260223 192.168.1.10 45406 typ host"
)
_CGNAT_HOST_CANDIDATE = (
    "candidate:1 1 udp 2122260223 100.64.1.10 45406 typ host"
)
_RELAY_CANDIDATE = (
    "candidate:1 1 udp 2122260223 100.64.1.10 49188 typ relay "
    "raddr 0.0.0.0 rport 0"
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
