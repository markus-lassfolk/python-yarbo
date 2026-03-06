"""Tests for yarbo.local — YarboLocalClient."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch
import zlib

import pytest

from yarbo.exceptions import YarboNotControllerError, YarboTimeoutError
from yarbo.local import YarboLocalClient
from yarbo.models import (
    HeadType,
    TelemetryEnvelope,
    YarboLightState,
    YarboPlan,
    YarboSchedule,
    YarboTelemetry,
)


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
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        mock_transport.connect.assert_called_once()

    async def test_disconnect_calls_transport(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        await client.disconnect()
        mock_transport.disconnect.assert_called_once()

    async def test_context_manager(self, mock_transport):
        async with YarboLocalClient(broker="192.0.2.1", sn="TEST123") as client:
            assert client.is_connected

    async def test_is_connected(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        assert client.is_connected is True

    async def test_serial_number(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="24400102L8HO5227")
        assert client.serial_number == "24400102L8HO5227"

    async def test_controller_acquired_default_false(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        assert client.controller_acquired is False

    async def test_controller_acquired_after_handshake(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value={"topic": "get_controller", "state": 0, "data": {}}
        )
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123", auto_controller=False)
        await client.connect()
        await client.get_controller()
        assert client.controller_acquired is True


@pytest.mark.asyncio
class TestYarboLocalClientLights:
    async def test_lights_on_publishes(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
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
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        await client.lights_off()
        call_args = mock_transport.publish.call_args
        payload = call_args[0][1]
        assert all(v == 0 for v in payload.values())

    async def test_set_lights_uses_state(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
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
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        await client.buzzer(state=1)
        call_args = mock_transport.publish.call_args
        assert call_args[0][0] == "cmd_buzzer"
        assert call_args[0][1]["state"] == 1
        assert "timeStamp" in call_args[0][1]

    async def test_buzzer_off(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        await client.buzzer(state=0)
        call_args = mock_transport.publish.call_args
        assert call_args[0][1]["state"] == 0


@pytest.mark.asyncio
class TestYarboLocalClientChute:
    async def test_set_chute(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
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
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123", auto_controller=True)
        await client.connect()
        assert client._controller_acquired is False
        await client.lights_on()
        # Should have published get_controller AND light_ctrl
        calls = [c[0][0] for c in mock_transport.publish.call_args_list]
        assert "get_controller" in calls
        assert "light_ctrl" in calls

    async def test_auto_controller_only_once(self, mock_transport):
        """get_controller is not sent again if already acquired."""
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
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
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123", auto_controller=False)
        await client.connect()
        with pytest.raises(YarboNotControllerError):
            await client.get_controller()

    async def test_controller_timeout_raises(self, mock_transport):
        """On timeout (None response from transport), get_controller raises YarboTimeoutError.

        The controller flag MUST NOT be set to True — the robot never acknowledged
        the handshake, so we cannot assume control was granted.
        """
        mock_transport.wait_for_message = AsyncMock(return_value=None)
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123", auto_controller=False)
        await client.connect()
        with pytest.raises(YarboTimeoutError):
            await client.get_controller()
        assert client._controller_acquired is False


@pytest.mark.asyncio
class TestYarboLocalClientTelemetry:
    async def test_get_status_derives_sn_from_topic_when_missing_from_payload(self, mock_transport):
        """get_status publishes get_device_msg and passes envelope topic to from_dict."""
        mock_transport.wait_for_message = AsyncMock(
            return_value={
                "topic": "snowbot/SN42/device/data_feedback",
                "payload": {"BatteryMSG": {"capacity": 50}},
            }
        )
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        result = await client.get_status(timeout=1.0)
        assert result is not None
        assert isinstance(result, YarboTelemetry)
        assert result.sn == "SN42"
        mock_transport.publish.assert_called_once()
        assert mock_transport.publish.call_args[0][0] == "get_device_msg"
        assert mock_transport.publish.call_args[0][1] == {}

    async def test_watch_telemetry_yields(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        items = []
        async for t in client.watch_telemetry():
            items.append(t)
            break  # only need one
        assert len(items) == 1

    async def test_watch_telemetry_merges_plan_feedback(self):
        """plan_feedback data is merged into the next DeviceMSG telemetry."""
        with patch("yarbo.local.MqttTransport") as MockT:  # noqa: N806
            instance = MagicMock()
            instance.connect = AsyncMock()
            instance.is_connected = True
            instance.add_reconnect_callback = MagicMock()

            async def fake_stream():
                yield TelemetryEnvelope(
                    kind="plan_feedback",
                    payload={
                        "planId": "p-123",
                        "state": "running",
                        "areaCovered": 55.0,
                        "duration": 120.0,
                    },
                    topic="snowbot/TEST/device/plan_feedback",
                )
                yield TelemetryEnvelope(
                    kind="DeviceMSG",
                    payload={"BatteryMSG": {"capacity": 70}, "StateMSG": {"working_state": 1}},
                    topic="snowbot/TEST/device/DeviceMSG",
                )

            instance.telemetry_stream = fake_stream
            MockT.return_value = instance

            client = YarboLocalClient(broker="192.0.2.1", sn="TEST")
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

    async def test_watch_telemetry_yields_data_feedback_telemetry(self):
        """watch_telemetry yields telemetry from data_feedback (get_device_msg response)."""
        with patch("yarbo.local.MqttTransport") as MockT:  # noqa: N806
            instance = MagicMock()
            instance.connect = AsyncMock()
            instance.is_connected = True
            instance.add_reconnect_callback = MagicMock()

            async def fake_stream():
                yield TelemetryEnvelope(
                    kind="data_feedback",
                    payload={"BatteryMSG": {"capacity": 60}, "StateMSG": {"working_state": 0}},
                    topic="snowbot/TEST/device/data_feedback",
                )

            instance.telemetry_stream = fake_stream
            MockT.return_value = instance

            client = YarboLocalClient(broker="192.0.2.1", sn="TEST")
            await client.connect()
            items = []
            async for t in client.watch_telemetry():
                items.append(t)
                break
            assert len(items) == 1
            assert items[0].battery == 60
            assert items[0].working_state == 0


@pytest.mark.asyncio
class TestYarboLocalClientPolling:
    """Tests for telemetry polling (get_device_msg keepalive)."""

    async def test_start_polling_stops_previous_task(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        await client.start_polling(interval_seconds=10.0)
        assert client.is_polling
        await client.start_polling(interval_seconds=15.0)
        assert client.is_polling
        await client.stop_polling()
        assert not client.is_polling

    async def test_stop_polling_no_op_when_not_polling(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        await client.stop_polling()
        assert not client.is_polling

    async def test_polling_interval_validation(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        with pytest.raises(ValueError, match="5.*3600"):
            await client.start_polling(interval_seconds=2.0)
        with pytest.raises(ValueError, match="5.*3600"):
            await client.start_polling(interval_seconds=4000.0)
        await client.start_polling(interval_seconds=10.0)
        await client.stop_polling()

    async def test_disconnect_stops_polling(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        await client.start_polling(interval_seconds=10.0)
        assert client.is_polling
        await client.disconnect()
        assert not client.is_polling

    async def test_is_polling_false_before_start(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        assert not client.is_polling


@pytest.mark.asyncio
class TestYarboLocalClientPlanManagement:
    """Tests for typed plan management methods (Issue #12)."""

    def _success_response(self, cmd: str) -> dict:
        return {"topic": cmd, "state": 0, "data": {}}

    async def test_start_plan_publishes_and_returns_result(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value=self._success_response("start_plan")
        )
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
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
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
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
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        result = await client.pause_plan()
        assert result.success is True

    async def test_resume_plan(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value=self._success_response("resume_plan")
        )
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        result = await client.resume_plan()
        assert result.success is True

    async def test_return_to_dock_uses_cmd_recharge(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value=self._success_response("cmd_recharge")
        )
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        result = await client.return_to_dock()
        cmds = [c[0][0] for c in mock_transport.publish.call_args_list]
        assert "cmd_recharge" in cmds
        assert result.success is True

    async def test_plan_timeout_raises(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(return_value=None)
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
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
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True  # skip get_controller so we only test timeout path
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
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True  # skip get_controller so mock returns schedule list
        result = await client.list_schedules()
        assert len(result) == 2
        assert isinstance(result[0], YarboSchedule)
        assert result[0].schedule_id == "s1"
        assert result[1].enabled is False

    async def test_set_schedule(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value={"topic": "save_schedule", "state": 0, "data": {}}
        )
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
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
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
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
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True  # skip get_controller so we only test timeout path
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
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True  # skip get_controller so mock returns plan list
        result = await client.list_plans()
        assert len(result) == 2
        assert isinstance(result[0], YarboPlan)
        assert result[0].plan_id == "p1"
        assert result[1].plan_name == "Back Yard"

    async def test_delete_plan(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value={"topic": "del_plan", "state": 0, "data": {}}
        )
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        result = await client.delete_plan("plan-id-1", confirm=True)
        assert result.success is True
        published = mock_transport.publish.call_args_list
        payload = next(c[0][1] for c in published if c[0][0] == "del_plan")
        assert payload["planId"] == "plan-id-1"


@pytest.mark.asyncio
class TestYarboLocalClientManualDrive:
    """Tests for manual drive command set."""

    async def test_start_manual_drive_publishes_set_working_state(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        await client.start_manual_drive()
        call_args = mock_transport.publish.call_args
        assert call_args[0][0] == "set_working_state"
        assert call_args[0][1] == {"state": "manual"}

    async def test_set_velocity_publishes_cmd_vel(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        await client.set_velocity(linear=0.5, angular=0.1)
        call_args = mock_transport.publish.call_args
        assert call_args[0][0] == "cmd_vel"
        assert call_args[0][1] == {"vel": 0.5, "rev": 0.1}

    async def test_set_velocity_default_angular(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        await client.set_velocity(linear=1.0)
        payload = mock_transport.publish.call_args[0][1]
        assert payload["vel"] == pytest.approx(1.0)
        assert payload["rev"] == pytest.approx(0.0)

    async def test_set_roller_publishes_cmd_roller(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        await client.set_roller(speed=1500)
        call_args = mock_transport.publish.call_args
        assert call_args[0][0] == "cmd_roller"
        assert call_args[0][1] == {"vel": 1500}

    async def test_stop_manual_drive_default_sends_dstop(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value={"topic": "dstop", "state": 0, "data": {}}
        )
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        result = await client.stop_manual_drive()
        cmds = [c[0][0] for c in mock_transport.publish.call_args_list]
        assert "dstop" in cmds
        assert result.success is True

    async def test_stop_manual_drive_hard_sends_dstopp(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value={"topic": "dstopp", "state": 0, "data": {}}
        )
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        result = await client.stop_manual_drive(hard=True)
        cmds = [c[0][0] for c in mock_transport.publish.call_args_list]
        assert "dstopp" in cmds
        assert result.success is True

    async def test_stop_manual_drive_emergency_sends_emergency_stop(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value={"topic": "emergency_stop_active", "state": 0, "data": {}}
        )
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        result = await client.stop_manual_drive(emergency=True)
        cmds = [c[0][0] for c in mock_transport.publish.call_args_list]
        assert "emergency_stop_active" in cmds
        assert result.success is True


@pytest.mark.asyncio
class TestYarboLocalClientGlobalParams:
    """Tests for global params read/save."""

    async def test_get_global_params_returns_dict(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value={
                "topic": "read_global_params",
                "state": 0,
                "data": {"speed": 0.8, "perimeterLaps": 2},
            }
        )
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True  # skip get_controller so mock returns params
        result = await client.get_global_params()
        assert result["speed"] == pytest.approx(0.8)
        assert result["perimeterLaps"] == 2
        cmds = [c[0][0] for c in mock_transport.publish.call_args_list]
        assert "read_global_params" in cmds

    async def test_get_global_params_empty_on_timeout(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(return_value=None)
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True  # skip get_controller so we only test timeout path
        result = await client.get_global_params(timeout=0.1)
        assert result == {}

    async def test_set_global_params_sends_cmd_save_para(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value={"topic": "cmd_save_para", "state": 0, "data": {}}
        )
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        params = {"speed": 0.6, "perimeterLaps": 3}
        result = await client.set_global_params(params)
        assert result.success is True
        published = mock_transport.publish.call_args_list
        payload = next(c[0][1] for c in published if c[0][0] == "cmd_save_para")
        assert payload["speed"] == pytest.approx(0.6)


@pytest.mark.asyncio
class TestYarboLocalClientMap:
    """Tests for map retrieval."""

    async def test_get_map_returns_dict(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value={
                "topic": "get_map",
                "state": 0,
                "data": {"areas": [{"id": "a1"}], "pathways": []},
            }
        )
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True  # skip get_controller so mock returns map
        result = await client.get_map()
        assert "areas" in result
        assert result["areas"][0]["id"] == "a1"
        cmds = [c[0][0] for c in mock_transport.publish.call_args_list]
        assert "get_map" in cmds

    async def test_get_map_empty_on_timeout(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(return_value=None)
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True  # skip get_controller so we only test timeout path
        result = await client.get_map(timeout=0.1)
        assert result == {}


@pytest.mark.asyncio
class TestYarboLocalClientHealth:
    """Tests for heartbeat tracking and is_healthy."""

    async def test_last_heartbeat_none_when_not_received(self, mock_transport):
        mock_transport.last_heartbeat = None
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        assert client.last_heartbeat is None

    async def test_last_heartbeat_returns_datetime(self, mock_transport):
        mock_transport.last_heartbeat = time.time()
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        assert isinstance(client.last_heartbeat, datetime)

    async def test_is_healthy_false_when_no_heartbeat(self, mock_transport):
        mock_transport.last_heartbeat = None
        mock_transport.is_connected = True
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        assert client.is_healthy() is False

    async def test_is_healthy_true_when_recent_heartbeat(self, mock_transport):
        mock_transport.last_heartbeat = time.time()
        mock_transport.is_connected = True
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        assert client.is_healthy(max_age_seconds=60.0) is True

    async def test_is_healthy_false_when_stale_heartbeat(self, mock_transport):
        mock_transport.last_heartbeat = time.time() - 120.0
        mock_transport.is_connected = True
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        assert client.is_healthy(max_age_seconds=60.0) is False


@pytest.mark.asyncio
class TestYarboLocalClientCreatePlan:
    """Tests for create_plan method."""

    async def test_create_plan_sends_save_plan(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value={"topic": "save_plan", "state": 0, "data": {}}
        )
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        result = await client.create_plan(name="Front Yard", area_ids=[1, 2, 3])
        assert result.success is True
        published = mock_transport.publish.call_args_list
        payload = next(c[0][1] for c in published if c[0][0] == "save_plan")
        assert payload["name"] == "Front Yard"
        assert payload["areaIds"] == [1, 2, 3]
        assert payload["enable_self_order"] is False

    async def test_create_plan_with_self_order(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value={"topic": "save_plan", "state": 0, "data": {}}
        )
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        await client.create_plan(name="Ordered Plan", area_ids=[5], enable_self_order=True)
        published = mock_transport.publish.call_args_list
        payload = next(c[0][1] for c in published if c[0][0] == "save_plan")
        assert payload["enable_self_order"] is True

    async def test_create_plan_timeout_raises(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(return_value=None)
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        with pytest.raises(YarboTimeoutError):
            await client.create_plan(name="X", area_ids=[1])


@pytest.mark.asyncio
class TestNewCommands:
    """Tests for newly added commands (#60)."""

    async def test_check_camera_status(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value={"topic": "check_camera_status", "state": 0, "data": {}}
        )
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        result = await client.check_camera_status()
        assert result.success is True
        mock_transport.publish.assert_any_call("check_camera_status", {})

    async def test_camera_calibration(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value={"topic": "camera_calibration", "state": 0, "data": {}}
        )
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        result = await client.camera_calibration()
        assert result.success is True

    async def test_firmware_update_now_requires_confirm(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        with pytest.raises(ValueError, match="confirm=True"):
            await client.firmware_update_now()

    async def test_firmware_update_now_with_confirm(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value={"topic": "firmware_update_now", "state": 0, "data": {}}
        )
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        result = await client.firmware_update_now(confirm=True)
        assert result.success is True
        mock_transport.publish.assert_any_call("firmware_update_now", {})

    async def test_firmware_update_tonight(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value={"topic": "firmware_update_tonight", "state": 0, "data": {}}
        )
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        result = await client.firmware_update_tonight()
        assert result.success is True

    async def test_firmware_update_later(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value={"topic": "firmware_update_later", "state": 0, "data": {}}
        )
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        result = await client.firmware_update_later()
        assert result.success is True

    async def test_get_saved_wifi_list(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(
            return_value={
                "topic": "get_saved_wifi_list",
                "state": 0,
                "data": {"wifiList": ["HomeNet"]},
            }
        )
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        result = await client.get_saved_wifi_list()
        assert "wifiList" in result

    async def test_get_saved_wifi_list_timeout(self, mock_transport):
        mock_transport.wait_for_message = AsyncMock(return_value=None)
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        result = await client.get_saved_wifi_list()
        assert result == {}

    async def test_bag_record_enabled(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        await client.bag_record(enabled=True)
        mock_transport.publish.assert_any_call("bag_record", {"state": 1})

    async def test_bag_record_disabled(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        client._controller_acquired = True
        await client.bag_record(enabled=False)
        mock_transport.publish.assert_any_call("bag_record", {"state": 0})


@pytest.mark.asyncio
class TestHeadValidation:
    """Tests for _validate_head_type and head-specific commands (#62)."""

    def _make_telemetry_with_head(self, head_type: int) -> YarboTelemetry:
        return YarboTelemetry(head_type=head_type)

    async def test_validate_no_status_warns_and_allows(self, mock_transport, caplog):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        # No _last_status set
        with caplog.at_level("WARNING", logger="yarbo.local"):
            client._validate_head_type(HeadType.LeafBlower)
        assert "unknown" in caplog.text.lower()

    async def test_validate_correct_head_passes(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        client._last_status = YarboTelemetry(head_type=HeadType.LeafBlower)
        # Should not raise
        client._validate_head_type(HeadType.LeafBlower)

    async def test_validate_wrong_head_raises(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        client._last_status = YarboTelemetry(head_type=HeadType.SnowBlower)
        with pytest.raises(ValueError, match="LeafBlower"):
            client._validate_head_type(HeadType.LeafBlower)

    async def test_validate_multiple_types_passes(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        client._last_status = YarboTelemetry(head_type=HeadType.LawnMowerPro)
        # Should not raise — LawnMowerPro is acceptable
        client._validate_head_type((HeadType.LawnMower, HeadType.LawnMowerPro))

    async def test_set_roller_speed_wrong_head_raises(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        client._last_status = YarboTelemetry(head_type=HeadType.SnowBlower)
        with pytest.raises(ValueError, match="LeafBlower"):
            await client.set_roller_speed(speed=1000)

    async def test_set_roller_speed_correct_head(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        client._controller_acquired = True
        client._last_status = YarboTelemetry(head_type=HeadType.LeafBlower)
        await client.set_roller_speed(speed=1000)
        mock_transport.publish.assert_any_call("set_roller_speed", {"speed": 1000})

    async def test_set_blade_height_wrong_head_raises(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        client._last_status = YarboTelemetry(head_type=HeadType.LeafBlower)
        with pytest.raises(ValueError, match="LawnMower"):
            await client.set_blade_height(height=3)

    async def test_set_blade_speed_wrong_head_raises(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        client._last_status = YarboTelemetry(head_type=HeadType.LeafBlower)
        with pytest.raises(ValueError, match="LawnMower"):
            await client.set_blade_speed(speed=80)

    async def test_push_snow_dir_wrong_head_raises(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        client._last_status = YarboTelemetry(head_type=HeadType.LeafBlower)
        with pytest.raises(ValueError, match="SnowBlower"):
            await client.push_snow_dir(direction=1)

    async def test_set_chute_steering_work_wrong_head_raises(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        client._last_status = YarboTelemetry(head_type=HeadType.LawnMower)
        with pytest.raises(ValueError, match="SnowBlower"):
            await client.set_chute_steering_work(state=1)

    async def test_push_snow_dir_correct_head(self, mock_transport):
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        client._controller_acquired = True
        client._last_status = YarboTelemetry(head_type=HeadType.SnowBlower)
        await client.push_snow_dir(direction=2)
        mock_transport.publish.assert_any_call("push_snow_dir", {"dir": 2})

    async def test_last_status_updated_by_get_status(self, mock_transport):
        """get_status() should update _last_status."""
        mock_transport.wait_for_message = AsyncMock(
            return_value={
                "topic": "snowbot/TEST123/device/DeviceMSG",
                "payload": {
                    "BatteryMSG": {"capacity": 75},
                    "HeadMSG": {"head_type": HeadType.LawnMower},
                },
            }
        )
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        await client.get_status()
        assert client._last_status is not None


# ---------------------------------------------------------------------------
# Queue release on CancelledError — issue #75
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestQueueReleaseOnCancelledError:
    """Verify that the pre-registered wait queue is released when CancelledError
    hits during publish(), i.e. before wait_for_message() can clean it up.

    Regression for issue #75: the previous ``except Exception`` guard silently
    leaked the queue because CancelledError is a BaseException, not Exception.
    """

    async def test_list_schedules_releases_queue_on_cancelled_publish(self, mock_transport):
        """Queue is released via release_queue() when publish raises CancelledError."""
        mock_transport.publish = AsyncMock(side_effect=asyncio.CancelledError())
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        with pytest.raises(asyncio.CancelledError):
            await client.list_schedules()
        mock_transport.release_queue.assert_called_once()

    async def test_list_plans_releases_queue_on_cancelled_publish(self, mock_transport):
        """Queue is released via release_queue() when publish raises CancelledError."""
        mock_transport.publish = AsyncMock(side_effect=asyncio.CancelledError())
        client = YarboLocalClient(broker="192.0.2.1", sn="TEST123")
        await client.connect()
        with pytest.raises(asyncio.CancelledError):
            await client.list_plans()
        mock_transport.release_queue.assert_called_once()
