"""Tests for yarbo.local — YarboLocalClient."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
import zlib

import pytest

from yarbo.exceptions import YarboNotControllerError, YarboTimeoutError
from yarbo.local import YarboLocalClient
from yarbo.models import TelemetryEnvelope, YarboLightState


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

    async def test_serial_number(self, mock_transport):
        client = YarboLocalClient(broker="192.168.1.24", sn="24400102L8HO5227")
        assert client.serial_number == "24400102L8HO5227"

    async def test_controller_acquired_default_false(self, mock_transport):
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        assert client.controller_acquired is False

    async def test_controller_acquired_after_handshake(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value={"topic": "get_controller", "state": 0, "data": {}}
        )
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123", auto_controller=False)
        await client.connect()
        await client.get_controller()
        assert client.controller_acquired is True


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
    async def test_watch_telemetry_yields(self, mock_transport):
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        items = []
        async for t in client.watch_telemetry():
            items.append(t)
            break  # only need one
        assert len(items) == 1

    async def test_watch_telemetry_merges_plan_feedback(self):
        """plan_feedback data is merged into the next DeviceMSG telemetry."""
        from unittest.mock import patch as _patch

        with _patch("yarbo.local.MqttTransport") as MockT:  # noqa: N806
            instance = MagicMock()
            instance.connect = AsyncMock()
            instance.is_connected = True
            instance.add_reconnect_callback = MagicMock()

            async def fake_stream():
                yield TelemetryEnvelope(
                    kind="plan_feedback",
                    payload={"planId": "p-123", "state": "running", "areaCovered": 55.0, "duration": 120.0},
                    topic="snowbot/TEST/device/plan_feedback",
                )
                yield TelemetryEnvelope(
                    kind="DeviceMSG",
                    payload={"BatteryMSG": {"capacity": 70}, "StateMSG": {"working_state": 1}},
                    topic="snowbot/TEST/device/DeviceMSG",
                )

            instance.telemetry_stream = fake_stream
            MockT.return_value = instance

            client = YarboLocalClient(broker="192.168.1.24", sn="TEST")
            await client.connect()
            items = []
            async for t in client.watch_telemetry():
                items.append(t)
                break
            assert len(items) == 1
            t = items[0]
            assert t.plan_id == "p-123"
            assert t.plan_state == "running"
            assert t.area_covered == pytest.approx(55.0)
            assert t.duration == pytest.approx(120.0)
            assert t.battery == 70


@pytest.mark.asyncio
class TestYarboLocalClientPlanManagement:
    """Tests for typed plan management methods (Issue #12)."""

    def _success_response(self, cmd: str) -> dict:
        return {"topic": cmd, "state": 0, "data": {}}

    async def test_start_plan_publishes_and_returns_result(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value=self._success_response("start_plan")
        )
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        result = await client.start_plan("plan-uuid-1")
        published = mock_transport.publish.call_args_list
        cmds = [c[0][0] for c in published]
        assert "start_plan" in cmds
        payload = next(c[0][1] for c in published if c[0][0] == "start_plan")
        assert payload["planId"] == "plan-uuid-1"
        assert result.success is True

    async def test_stop_plan(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value=self._success_response("stop_plan")
        )
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        result = await client.stop_plan()
        cmds = [c[0][0] for c in mock_transport.publish.call_args_list]
        assert "stop_plan" in cmds
        assert result.success is True

    async def test_pause_plan(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value=self._success_response("pause_plan")
        )
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        result = await client.pause_plan()
        assert result.success is True

    async def test_resume_plan(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value=self._success_response("resume_plan")
        )
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        result = await client.resume_plan()
        assert result.success is True

    async def test_return_to_dock_uses_cmd_recharge(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value=self._success_response("cmd_recharge")
        )
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        result = await client.return_to_dock()
        cmds = [c[0][0] for c in mock_transport.publish.call_args_list]
        assert "cmd_recharge" in cmds
        assert result.success is True

    async def test_plan_timeout_raises(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(return_value=None)
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        with pytest.raises(YarboTimeoutError):
            await client.start_plan("p1")


@pytest.mark.asyncio
class TestYarboLocalClientScheduleManagement:
    """Tests for schedule management API (Issue #14)."""

    async def test_list_schedules_empty(self, mock_transport):
        """list_schedules returns empty list on timeout."""
        mock_transport.wait_for_message = AsyncMock(return_value=None)
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        result = await client.list_schedules(timeout=0.1)
        assert result == []

    async def test_list_schedules_returns_schedule_objects(self, mock_transport):
        schedules_data = [
            {"scheduleId": "s1", "planId": "p1", "enabled": True, "weekdays": [1, 3]},
            {"scheduleId": "s2", "planId": "p2", "enabled": False, "weekdays": [5]},
        ]
        mock_transport.wait_for_message = AsyncMock(
            return_value={
                "topic": "read_all_schedule",
                "state": 0,
                "data": {"scheduleList": schedules_data},
            }
        )
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        result = await client.list_schedules()
        assert len(result) == 2
        from yarbo.models import YarboSchedule
        assert isinstance(result[0], YarboSchedule)
        assert result[0].schedule_id == "s1"
        assert result[1].enabled is False

    async def test_set_schedule(self, mock_transport):
        from yarbo.models import YarboSchedule
        mock_transport.wait_for_message = AsyncMock(
            return_value={"topic": "save_schedule", "state": 0, "data": {}}
        )
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        sched = YarboSchedule(
            schedule_id="s1",
            plan_id="p1",
            enabled=True,
            weekdays=[1, 5],
            start_time="08:00",
        )
        result = await client.set_schedule(sched)
        assert result.success is True
        cmds = [c[0][0] for c in mock_transport.publish.call_args_list]
        assert "save_schedule" in cmds

    async def test_delete_schedule(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value={"topic": "del_schedule", "state": 0, "data": {}}
        )
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        result = await client.delete_schedule("sched-id-1")
        assert result.success is True
        published = mock_transport.publish.call_args_list
        payload = next(c[0][1] for c in published if c[0][0] == "del_schedule")
        assert payload["scheduleId"] == "sched-id-1"


@pytest.mark.asyncio
class TestYarboLocalClientPlanCRUD:
    """Tests for Plan CRUD API (Issue #15)."""

    async def test_list_plans_empty_on_timeout(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(return_value=None)
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        result = await client.list_plans(timeout=0.1)
        assert result == []

    async def test_list_plans_returns_plan_objects(self, mock_transport):
        plans_data = [
            {"planId": "p1", "planName": "Front Yard"},
            {"planId": "p2", "planName": "Back Yard"},
        ]
        mock_transport.wait_for_message = AsyncMock(
            return_value={
                "topic": "read_all_plan",
                "state": 0,
                "data": {"planList": plans_data},
            }
        )
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        result = await client.list_plans()
        assert len(result) == 2
        from yarbo.models import YarboPlan
        assert isinstance(result[0], YarboPlan)
        assert result[0].plan_id == "p1"
        assert result[1].plan_name == "Back Yard"

    async def test_delete_plan(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value={"topic": "del_plan", "state": 0, "data": {}}
        )
        client = YarboLocalClient(broker="192.168.1.24", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        result = await client.delete_plan("plan-id-1")
        assert result.success is True
        published = mock_transport.publish.call_args_list
        payload = next(c[0][1] for c in published if c[0][0] == "del_plan")
        assert payload["planId"] == "plan-id-1"
