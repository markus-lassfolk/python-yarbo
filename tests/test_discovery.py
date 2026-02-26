"""Tests for yarbo.discovery — auto-discovery of Yarbo robots."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from yarbo.discovery import DiscoveredRobot, discover_yarbo


class TestDiscoveredRobot:
    def test_repr_with_sn(self):
        r = DiscoveredRobot(broker_host="192.168.1.24", broker_port=1883, sn="ABC123")
        assert "192.168.1.24" in repr(r)
        assert "ABC123" in repr(r)

    def test_repr_without_sn(self):
        r = DiscoveredRobot(broker_host="192.168.1.24", broker_port=1883)
        assert "1883" in repr(r)


@pytest.mark.asyncio
class TestDiscoverYarbo:
    async def test_returns_list(self):
        with patch("yarbo.discovery._probe_broker", side_effect=OSError("unreachable")):
            result = await discover_yarbo(timeout=0.1)
            assert isinstance(result, list)

    async def test_finds_broker(self):
        robot = DiscoveredRobot(broker_host="192.168.1.24", broker_port=1883, sn="XYZ")
        with patch(
            "yarbo.discovery._probe_broker",
            side_effect=lambda host, port, timeout: (
                robot if host == "192.168.1.24" else OSError("unreachable")
            ),
        ):
            result = await discover_yarbo(timeout=0.5)
            assert any(r.broker_host == "192.168.1.24" for r in result)

    async def test_empty_when_none_found(self):
        with patch("yarbo.discovery._probe_broker", side_effect=OSError("no broker")):
            result = await discover_yarbo(timeout=0.1)
            assert result == []

    async def test_deduplicates_candidates(self):
        """Known IPs should not appear twice."""
        called_ips: list[str] = []

        async def mock_probe(host, port, timeout):
            called_ips.append(host)
            raise OSError("unreachable")

        with patch("yarbo.discovery._probe_broker", side_effect=mock_probe):
            await discover_yarbo(timeout=0.1)
        assert len(called_ips) == len(set(called_ips))
