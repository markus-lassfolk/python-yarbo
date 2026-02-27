"""Tests for yarbo.client — YarboClient (hybrid orchestrator)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from yarbo.client import YarboClient
from yarbo.models import YarboLightState, YarboTelemetry


@pytest.fixture
def mock_local_client():
    """Replace YarboLocalClient with a mock in YarboClient."""
    with patch("yarbo.client.YarboLocalClient") as MockLocal:  # noqa: N806
        instance = MagicMock()
        instance.connect = AsyncMock()
        instance.disconnect = AsyncMock()
        instance.lights_on = AsyncMock()
        instance.lights_off = AsyncMock()
        instance.set_lights = AsyncMock()
        instance.buzzer = AsyncMock()
        instance.set_chute = AsyncMock()
        instance.get_controller = AsyncMock(return_value=None)
        instance.get_status = AsyncMock(return_value=None)
        instance.publish_raw = AsyncMock()
        instance.is_connected = True

        async def fake_stream():
            t = MagicMock(spec=YarboTelemetry)
            t.battery = 75
            yield t

        instance.watch_telemetry = fake_stream
        MockLocal.return_value = instance
        yield instance


@pytest.mark.asyncio
class TestYarboClientLifecycle:
    async def test_context_manager(self, mock_local_client):
        async with YarboClient(broker="192.168.0.1", sn="TEST") as client:
            assert client.is_connected is True
        mock_local_client.connect.assert_called_once()
        mock_local_client.disconnect.assert_called_once()

    async def test_connect_disconnect(self, mock_local_client):
        client = YarboClient(broker="192.168.0.1", sn="TEST")
        await client.connect()
        await client.disconnect()
        mock_local_client.connect.assert_called_once()
        mock_local_client.disconnect.assert_called_once()

    async def test_serial_number(self, mock_local_client):
        mock_local_client.serial_number = "24400102L8HO5227"
        client = YarboClient(broker="192.168.0.1", sn="24400102L8HO5227")
        assert client.serial_number == "24400102L8HO5227"

    async def test_controller_acquired_false_by_default(self, mock_local_client):
        """controller_acquired delegates to the local client and is False before handshake."""
        mock_local_client.controller_acquired = False
        client = YarboClient(broker="192.168.0.1", sn="TEST")
        assert client.controller_acquired is False

    async def test_controller_acquired_true_after_handshake(self, mock_local_client):
        """controller_acquired reflects the local client's state after get_controller."""
        mock_local_client.controller_acquired = True
        client = YarboClient(broker="192.168.0.1", sn="TEST")
        assert client.controller_acquired is True


@pytest.mark.asyncio
class TestYarboClientDelegation:
    async def test_lights_on(self, mock_local_client):
        async with YarboClient(broker="192.168.0.1", sn="TEST") as client:
            await client.lights_on()
        mock_local_client.lights_on.assert_called_once()

    async def test_lights_off(self, mock_local_client):
        async with YarboClient(broker="192.168.0.1", sn="TEST") as client:
            await client.lights_off()
        mock_local_client.lights_off.assert_called_once()

    async def test_buzzer(self, mock_local_client):
        async with YarboClient(broker="192.168.0.1", sn="TEST") as client:
            await client.buzzer(state=1)
        mock_local_client.buzzer.assert_called_once_with(state=1)

    async def test_set_chute(self, mock_local_client):
        async with YarboClient(broker="192.168.0.1", sn="TEST") as client:
            await client.set_chute(vel=45)
        mock_local_client.set_chute.assert_called_once_with(vel=45)

    async def test_set_lights(self, mock_local_client):
        state = YarboLightState(led_head=100)
        async with YarboClient(broker="192.168.0.1", sn="TEST") as client:
            await client.set_lights(state)
        mock_local_client.set_lights.assert_called_once_with(state)

    async def test_publish_raw(self, mock_local_client):
        async with YarboClient(broker="192.168.0.1", sn="TEST") as client:
            await client.publish_raw("start_plan", {"planId": "p1"})
        mock_local_client.publish_raw.assert_called_once_with("start_plan", {"planId": "p1"})


@pytest.mark.asyncio
class TestYarboClientTelemetry:
    async def test_watch_telemetry(self, mock_local_client):
        async with YarboClient(broker="192.168.0.1", sn="TEST") as client:
            items = []
            async for t in client.watch_telemetry():
                items.append(t)
                break
        assert len(items) == 1
        assert items[0].battery == 75


@pytest.mark.asyncio
class TestYarboClientCloud:
    async def test_list_robots_connects_cloud(self, mock_local_client):
        with patch("yarbo.client.YarboCloudClient") as MockCloud:  # noqa: N806
            cloud_instance = MagicMock()
            cloud_instance.connect = AsyncMock()
            cloud_instance.disconnect = AsyncMock()
            cloud_instance.list_robots = AsyncMock(return_value=[])
            MockCloud.return_value = cloud_instance

            async with YarboClient(
                broker="192.168.0.1",
                sn="TEST",
                username="user@example.com",
                password="secret",
            ) as client:
                robots = await client.list_robots()

            assert robots == []
            cloud_instance.connect.assert_called_once()
