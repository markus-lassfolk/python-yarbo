"""Tests for yarbo.local — YarboLocalClient."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
import zlib

import pytest

from yarbo.exceptions import YarboNotControllerError, YarboTimeoutError
from yarbo.local import YarboLocalClient
from yarbo.models import TelemetryEnvelope, YarboLightState, YarboTelemetry


def _encode(payload: dict) -> bytes:
    return zlib.compress(json.dumps(payload).encode())


@pytest.fixture
def mock_transport():
    """Mock MqttTransport for unit testing without a real broker."""
    with patch("yarbo.local.MqttTransport") as MockTransport:  # noqa: N806
        instance = MagicMock()
        instance.connect = AsyncMock()
        instance.disconnect = AsyncMock()
        instance.publish = AsyncMock()
        instance.wait_for_message = AsyncMock(return_value=None)
        instance.create_wait_queue = MagicMock(return_value=MagicMock())
        instance.is_connected = True

        # telemetry_stream yields TelemetryEnvelope objects (DeviceMSG kind)
        async def fake_stream():
            yield TelemetryEnvelope(
                kind="DeviceMSG",
                payload={"BatteryMSG": {"capacity": 85}, "StateMSG": {"working_state": 0}},
                topic="snowbot/TEST123/device/DeviceMSG",
            )

        instance.telemetry_stream = fake_stream
        MockTransport.return_value = instance
        yield instance


@pytest.mark.asyncio
class TestYarboLocalClientConnect:
    async def test_connect_calls_transport(self, mock_transport):
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        mock_transport.connect.assert_called_once()

    async def test_disconnect_calls_transport(self, mock_transport):
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        await client.disconnect()
        mock_transport.disconnect.assert_called_once()

    async def test_context_manager(self, mock_transport):
        async with YarboLocalClient(broker="192.168.1.24", sn="TEST123") as client:
            assert client.is_connected

    async def test_is_connected(self, mock_transport):
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        assert client.is_connected is True


@pytest.mark.asyncio
class TestYarboLocalClientLights:
    async def test_lights_on_publishes(self, mock_transport):
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        client._controller_acquired = True  # skip handshake
        await client.lights_on()
        mock_transport.publish.assert_called_once()
        call_args = mock_transport.publish.call_args
        assert call_args[0][0] == "light_ctrl"
        payload = call_args[0][1]
        assert payload["led_head"] == 255
        assert payload["body_left_r"] == 255

    async def test_lights_off_publishes(self, mock_transport):
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        await client.lights_off()
        call_args = mock_transport.publish.call_args
        payload = call_args[0][1]
        assert all(v == 0 for v in payload.values())

    async def test_set_lights_uses_state(self, mock_transport):
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        state = YarboLightState(led_head=100, body_left_r=50)
        await client.set_lights(state)
        call_args = mock_transport.publish.call_args
        assert call_args[0][1]["led_head"] == 100
        assert call_args[0][1]["body_left_r"] == 50


@pytest.mark.asyncio
class TestYarboLocalClientBuzzer:
    async def test_buzzer_on(self, mock_transport):
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        await client.buzzer(state=1)
        call_args = mock_transport.publish.call_args
        assert call_args[0][0] == "cmd_buzzer"
        assert call_args[0][1]["state"] == 1
        assert "timeStamp" in call_args[0][1]

    async def test_buzzer_off(self, mock_transport):
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        await client.buzzer(state=0)
        call_args = mock_transport.publish.call_args
        assert call_args[0][1]["state"] == 0


@pytest.mark.asyncio
class TestYarboLocalClientChute:
    async def test_set_chute(self, mock_transport):
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        await client.set_chute(vel=90)
        call_args = mock_transport.publish.call_args
        assert call_args[0][0] == "cmd_chute"
        assert call_args[0][1]["vel"] == 90


@pytest.mark.asyncio
class TestYarboLocalClientController:
    async def test_auto_controller_fires_on_first_command(self, mock_transport):
        """get_controller is called automatically before the first action."""
        # Return a successful command result (state=0)
        mock_transport.wait_for_message = AsyncMock(
            return_value={"topic": "get_controller", "state": 0, "data": {}}
        )
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123", auto_controller=True)
        await client.connect()
        assert client._controller_acquired is False
        await client.lights_on()
        # Should have published get_controller AND light_ctrl
        calls = [c[0][0] for c in mock_transport.publish.call_args_list]
        assert "get_controller" in calls
        assert "light_ctrl" in calls

    async def test_auto_controller_only_once(self, mock_transport):
        """get_controller is not sent again if already acquired."""
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        await client.lights_on()
        await client.lights_off()
        calls = [c[0][0] for c in mock_transport.publish.call_args_list]
        assert calls.count("get_controller") == 0

    async def test_controller_rejected_raises(self, mock_transport):
        """Robot rejecting the handshake raises YarboNotControllerError."""
        mock_transport.wait_for_message = AsyncMock(
            return_value={"topic": "get_controller", "state": 1, "data": {}}
        )
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123", auto_controller=False)
        await client.connect()
        with pytest.raises(YarboNotControllerError):
            await client.get_controller()

    async def test_controller_timeout_raises(self, mock_transport):
        """On timeout (None response from transport), get_controller raises YarboTimeoutError.

        The controller flag MUST NOT be set to True — the robot never acknowledged
        the handshake, so we cannot assume control was granted.
        """
        mock_transport.wait_for_message = AsyncMock(return_value=None)
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123", auto_controller=False)
        await client.connect()
        with pytest.raises(YarboTimeoutError):
            await client.get_controller()
        assert client._controller_acquired is False


@pytest.mark.asyncio
class TestYarboLocalClientTelemetry:
    async def test_get_status_derives_sn_from_topic_when_missing_from_payload(self, mock_transport):
        """get_status passes envelope topic to from_dict so sn is derived when absent."""
        mock_transport.wait_for_message = AsyncMock(
            return_value={
                "topic": "snowbot/SN42/device/DeviceMSG",
                "payload": {"BatteryMSG": {"capacity": 50}},
            }
        )
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        result = await client.get_status(timeout=1.0)
        assert result is not None
        assert isinstance(result, YarboTelemetry)
        assert result.sn == "SN42"

    async def test_watch_telemetry_yields(self, mock_transport):
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        items = []
        async for t in client.watch_telemetry():
            items.append(t)
            break  # only need one
        assert len(items) == 1
