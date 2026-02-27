"""Tests for yarbo.discovery — auto-discovery of Yarbo robots."""

from __future__ import annotations

import ipaddress
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from yarbo.discovery import (
    DEFAULT_MAX_HOSTS_PER_SUBNET,
    DiscoveredRobot,
    YarboEndpoint,
    _expand_subnet,
    _get_local_subnets,
    _hostname_indicates_dc,
    connection_order,
    discover_yarbo,
)


class TestGetLocalSubnets:
    """Tests for _get_local_subnets (dynamic host network detection)."""

    def test_linux_parses_ip_addr_output(self):
        """Linux: ip -4 -o addr show output is parsed to CIDRs."""
        fake_stdout = "2: eth0    inet 192.168.1.10/24 brd 192.168.1.255 scope global eth0\n"
        fake_stdout += "3: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\n"
        with patch("sys.platform", "linux"), patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout=fake_stdout),
        ):
            subnets = _get_local_subnets()
        assert "192.168.1.0/24" in subnets
        assert "172.17.0.0/16" in subnets
        assert len(subnets) == 2

    def test_loopback_excluded(self):
        """127.0.0.0/8 is never returned."""
        fake_stdout = "1: lo    inet 127.0.0.1/8 scope host lo\n"
        with patch("sys.platform", "linux"), patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout=fake_stdout),
        ):
            subnets = _get_local_subnets()
        assert not any("127." in s for s in subnets)

    def test_returns_empty_on_unknown_platform(self):
        """Non-Linux/Darwin/Windows returns empty list."""
        with patch("sys.platform", "unknown"):
            subnets = _get_local_subnets()
        assert subnets == []


class TestExpandSubnet:
    """Tests for _expand_subnet (max_hosts cap per subnet)."""

    def test_caps_large_subnet(self):
        """Subnet with more than max_hosts is capped; was_capped True."""
        network = ipaddress.ip_network("192.168.1.0/24", strict=False)
        ips, capped = _expand_subnet(network, max_hosts=3)
        assert len(ips) == 3
        assert capped is True
        assert ips[0] == "192.168.1.1"

    def test_small_subnet_not_capped(self):
        """Subnet with fewer hosts than max_hosts returns all; was_capped False."""
        network = ipaddress.ip_network("192.168.1.0/30", strict=False)
        ips, capped = _expand_subnet(network, max_hosts=10)
        assert len(ips) == 2
        assert capped is False

    def test_default_max_hosts_constant(self):
        """DEFAULT_MAX_HOSTS_PER_SUBNET is 512."""
        assert DEFAULT_MAX_HOSTS_PER_SUBNET == 512


class TestHostnameIndicatesDc:
    def test_yarbo_in_hostname(self):
        assert _hostname_indicates_dc("YARBO") is True
        assert _hostname_indicates_dc("yarbo-bridge") is True
        assert _hostname_indicates_dc("something-YARBO-local") is True

    def test_no_match(self):
        assert _hostname_indicates_dc("rover") is False
        assert _hostname_indicates_dc(None) is False
        assert _hostname_indicates_dc("") is False


class TestConnectionOrder:
    def test_recommended_first(self):
        a = YarboEndpoint("192.168.1.24", 1883, "rover", "", False, None, "")
        b = YarboEndpoint("192.168.1.55", 1883, "dc", "", True, "YARBO", "SN1")
        ordered = connection_order([a, b])
        assert ordered[0] is b
        assert ordered[1] is a

    def test_no_recommended_unchanged(self):
        a = YarboEndpoint("192.168.1.24", 1883, "rover", "", False, None, "")
        b = YarboEndpoint("192.168.1.55", 1883, "rover", "", False, None, "")
        ordered = connection_order([a, b])
        assert ordered == [a, b]

    def test_empty(self):
        assert connection_order([]) == []


class TestDiscoveredRobot:
    def test_repr_with_sn(self):
        r = DiscoveredRobot(broker_host="192.168.0.1", broker_port=1883, sn="ABC123")
        assert "192.168.0.1" in repr(r)
        assert "ABC123" in repr(r)

    def test_repr_without_sn(self):
        r = DiscoveredRobot(broker_host="192.168.0.1", broker_port=1883)
        assert "1883" in repr(r)


@pytest.mark.asyncio
class TestDiscoverYarbo:
    async def test_returns_list(self):
        with patch("yarbo.discovery.discover", return_value=[]):
            result = await discover_yarbo(timeout=0.1)
            assert isinstance(result, list)

    async def test_finds_broker(self):
        endpoint = YarboEndpoint(
            ip="192.168.0.1",
            port=1883,
            path="rover",
            mac="c8:fe:0f:ff:74:56",
            recommended=True,
            hostname=None,
            sn="XYZ",
        )
        with patch("yarbo.discovery.discover", return_value=[endpoint]):
            result = await discover_yarbo(timeout=0.5)
            assert any(r.broker_host == "192.168.0.1" for r in result)
            assert result[0].sn == "XYZ"

    async def test_empty_when_none_found(self):
        with patch("yarbo.discovery.discover", return_value=[]):
            result = await discover_yarbo(timeout=0.1)
            assert result == []

    async def test_deduplicates_candidates(self):
        """Known IPs should not be probed twice."""
        probed: list[str] = []

        async def record_heartbeat(host: str, port: int, timeout: float):
            probed.append(host)
            return (False, "")

        async def fake_connection(host, port, **kwargs):
            writer = MagicMock()
            writer.close = lambda: None
            writer.wait_closed = AsyncMock(return_value=None)
            return (None, writer)

        with (
            patch("yarbo.discovery._get_local_subnets", return_value=["192.168.1.0/30"]),
            patch("yarbo.discovery.asyncio.open_connection", side_effect=fake_connection),
            patch("yarbo.discovery._verify_yarbo_heartbeat", side_effect=record_heartbeat),
            patch("yarbo.discovery._get_mac_for_ip", return_value=""),
            patch("yarbo.discovery._get_hostname_for_ip", return_value=None),
        ):
            await discover_yarbo(timeout=0.1)
        assert len(probed) == len(set(probed))
