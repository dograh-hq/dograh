from api.routes.webrtc_signaling import is_private_ip_candidate


class TestIsPrivateIpCandidate:
    """Tests for is_private_ip_candidate function."""

    def test_private_ip_192_168(self):
        """192.168.x.x addresses are detected as private."""
        candidate = (
            "candidate:123 1 udp 2122260223 192.168.50.24 63603 typ host generation 0"
        )
        assert is_private_ip_candidate(candidate) is True

    def test_private_ip_10_x(self):
        """10.x.x.x addresses are detected as private."""
        candidate = (
            "candidate:456 1 udp 2122260223 10.0.0.1 12345 typ host generation 0"
        )
        assert is_private_ip_candidate(candidate) is True

    def test_private_ip_172_16(self):
        """172.16.x.x addresses are detected as private."""
        candidate = (
            "candidate:789 1 udp 2122260223 172.16.0.1 54321 typ host generation 0"
        )
        assert is_private_ip_candidate(candidate) is True

    def test_private_ip_172_31(self):
        """172.31.x.x addresses are detected as private."""
        candidate = (
            "candidate:101 1 udp 2122260223 172.31.255.255 12345 typ host generation 0"
        )
        assert is_private_ip_candidate(candidate) is True

    def test_cgnat_ip(self):
        """CGNAT addresses (100.64.0.0/10) are detected as private."""
        candidate = (
            "candidate:202 1 udp 2122260223 100.64.0.1 12345 typ host generation 0"
        )
        assert is_private_ip_candidate(candidate) is True

    def test_cgnat_ip_upper_bound(self):
        """Upper bound of CGNAT range is detected."""
        candidate = (
            "candidate:303 1 udp 2122260223 100.127.255.255 12345 typ host generation 0"
        )
        assert is_private_ip_candidate(candidate) is True

    def test_public_ip(self):
        """Public IP addresses return False."""
        candidate = (
            "candidate:404 1 udp 2122260223 142.250.190.46 12345 typ host generation 0"
        )
        assert is_private_ip_candidate(candidate) is False

    def test_public_ip_8_8_8_8(self):
        """Google DNS (8.8.8.8) is detected as public."""
        candidate = "candidate:505 1 udp 2122260223 8.8.8.8 12345 typ host generation 0"
        assert is_private_ip_candidate(candidate) is False

    def test_non_cgnat_100_range(self):
        """100.x.x.x outside CGNAT range is public."""
        candidate = (
            "candidate:606 1 udp 2122260223 100.128.0.1 12345 typ host generation 0"
        )
        assert is_private_ip_candidate(candidate) is False

    def test_172_15_is_public(self):
        """172.15.x.x is outside private range and should be public."""
        candidate = (
            "candidate:707 1 udp 2122260223 172.15.255.255 12345 typ srflx generation 0"
        )
        assert is_private_ip_candidate(candidate) is False

    def test_172_32_is_public(self):
        """172.32.x.x is outside private range and should be public."""
        candidate = (
            "candidate:808 1 udp 2122260223 172.32.0.1 12345 typ srflx generation 0"
        )
        assert is_private_ip_candidate(candidate) is False

    def test_srflx_candidate_type(self):
        """Server reflexive candidates are parsed correctly."""
        candidate = "candidate:909 1 udp 1686052607 142.250.190.46 45678 typ srflx raddr 192.168.1.1 rport 12345"
        assert is_private_ip_candidate(candidate) is False

    def test_relay_candidate_type(self):
        """Relay candidates are parsed correctly."""
        candidate = (
            "candidate:111 1 udp 41885439 1.1.1.1 50000 typ relay raddr 0.0.0.0 rport 0"
        )
        assert is_private_ip_candidate(candidate) is False

    def test_loopback_ip(self):
        """Loopback addresses are detected as private."""
        candidate = (
            "candidate:222 1 udp 2122260223 127.0.0.1 12345 typ host generation 0"
        )
        assert is_private_ip_candidate(candidate) is True

    def test_link_local_ip(self):
        """Link-local addresses (169.254.x.x) are detected as private."""
        candidate = (
            "candidate:333 1 udp 2122260223 169.254.1.1 12345 typ host generation 0"
        )
        assert is_private_ip_candidate(candidate) is True

    def test_ipv6_link_local(self):
        """IPv6 link-local addresses are detected as private."""
        candidate = "candidate:444 1 udp 2122260223 fe80::1 12345 typ host generation 0"
        assert is_private_ip_candidate(candidate) is True

    def test_ipv6_public(self):
        """Public IPv6 addresses return False."""
        candidate = "candidate:555 1 udp 2122260223 2001:4860:4860::8888 12345 typ host generation 0"
        assert is_private_ip_candidate(candidate) is False

    def test_malformed_candidate_no_typ(self):
        """Malformed candidate without 'typ' returns False."""
        candidate = "candidate:666 1 udp 2122260223 192.168.1.1 12345"
        assert is_private_ip_candidate(candidate) is False

    def test_malformed_candidate_empty(self):
        """Empty candidate string returns False."""
        assert is_private_ip_candidate("") is False

    def test_malformed_candidate_invalid_ip(self):
        """Candidate with invalid IP returns False."""
        candidate = (
            "candidate:777 1 udp 2122260223 not.an.ip 12345 typ host generation 0"
        )
        assert is_private_ip_candidate(candidate) is False

    def test_malformed_candidate_short(self):
        """Candidate with too few parts returns False."""
        candidate = "candidate:888 typ host"
        assert is_private_ip_candidate(candidate) is False

    def test_tcp_candidate(self):
        """TCP candidates are parsed correctly."""
        candidate = (
            "candidate:999 1 tcp 1518280447 192.168.1.100 9 typ host tcptype active"
        )
        assert is_private_ip_candidate(candidate) is True
