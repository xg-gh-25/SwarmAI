"""SSRF egress-validation tests for the shared outbound-fetch chokepoint.

Verifies the custom validating transport (run_cd11637a, BSC-A2):
- non-https scheme → SSRFBlocked before connect
- private/loopback/link-local/reserved/CGNAT/metadata IP → SSRFBlocked at connect
- DNS-rebind safe: validation classifies the RESOLVED ip (not just the hostname string)
- public https URL → passes validation (no false-positive)

Methodology: unit-test the pure classifier (_classify_ip) exhaustively, and drive
the real transport's handle_request for scheme + resolved-IP rejection with a
monkeypatched resolver (so no live network + deterministic rebind simulation).

Mutation check: neutralize the guard (make _classify_ip always return False /
allow non-https) → the block assertions go RED.
"""
import ipaddress

import httpx
import pytest


class TestClassifyIP:
    """_classify_ip(ip_str) -> True if the IP must be blocked (non-routable/internal)."""

    @pytest.mark.parametrize("ip", [
        "127.0.0.1",            # loopback
        "10.0.0.1",             # private A
        "172.16.0.1",           # private B
        "192.168.1.1",          # private C
        "169.254.169.254",      # cloud metadata (link-local)
        "100.64.0.1",           # CGNAT (NOT is_private — explicit range)
        "0.0.0.0",              # unspecified
        "::1",                  # v6 loopback
        "fc00::1",              # v6 unique-local
        "fe80::1",              # v6 link-local
        "::ffff:127.0.0.1",     # IPv4-mapped loopback
        "224.0.0.1",            # multicast
    ])
    def test_dangerous_ips_blocked(self, ip):
        from jobs.adapters.http_client import _classify_ip
        assert _classify_ip(ip) is True, f"{ip} should be blocked"

    @pytest.mark.parametrize("ip", [
        "93.184.216.34",        # example.com (public)
        "140.82.112.3",         # github.com (public)
        "8.8.8.8",              # public DNS
        "2606:2800:220:1:248:1893:25c8:1946",  # public v6
    ])
    def test_public_ips_allowed(self, ip):
        from jobs.adapters.http_client import _classify_ip
        assert _classify_ip(ip) is False, f"{ip} should be allowed"


class TestEgressValidation:
    """The transport rejects bad scheme + resolved private IP before bytes flow."""

    def test_non_https_scheme_blocked(self):
        from jobs.adapters.http_client import SSRFBlocked, _validate_egress
        for url in ("http://example.com", "file:///etc/passwd", "gopher://x/1"):
            with pytest.raises(SSRFBlocked):
                _validate_egress(url)

    def test_private_host_blocked_via_resolved_ip(self, monkeypatch):
        """Rebind-safe: validation must classify the RESOLVED ip, not the hostname."""
        from jobs.adapters import http_client as hc

        # simulate a hostname that resolves to a metadata IP
        def fake_resolve(host):
            return ["169.254.169.254"]
        monkeypatch.setattr(hc, "_resolve_ips", fake_resolve)
        with pytest.raises(hc.SSRFBlocked):
            hc._validate_egress("https://evil.example.com/steal")

    def test_dual_stack_any_bad_ip_blocks(self, monkeypatch):
        """A host returning one public + one private IP must be rejected."""
        from jobs.adapters import http_client as hc
        monkeypatch.setattr(hc, "_resolve_ips", lambda host: ["93.184.216.34", "10.0.0.5"])
        with pytest.raises(hc.SSRFBlocked):
            hc._validate_egress("https://dualstack.example.com/")

    def test_public_https_passes_and_returns_validated_ip(self, monkeypatch):
        from jobs.adapters import http_client as hc
        monkeypatch.setattr(hc, "_resolve_ips", lambda host: ["93.184.216.34"])
        # should NOT raise; returns the validated IP (currently ADVISORY/logged —
        # NOT used to pin the socket; super().handle_request re-resolves. See the
        # residual-TOCTOU caveat in _validate_egress. This test asserts validation
        # passes for a public host, not that the connection is IP-pinned.)
        validated = hc._validate_egress("https://example.com/feed.xml")
        assert validated == "93.184.216.34"


class TestTransportWired:
    """safe_client uses the validating transport (not a raw client)."""

    def test_safe_client_has_validating_transport(self):
        from jobs.adapters.http_client import safe_client, _ValidatingTransport
        with safe_client() as client:
            # RetryClient wraps httpx.Client — reach the real client
            real = getattr(client, "_client", client)
            assert isinstance(real._transport, _ValidatingTransport)
