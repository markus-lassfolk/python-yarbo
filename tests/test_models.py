"""Tests for yarbo.models — dataclass parsing and factories."""

from __future__ import annotations

import pytest

from yarbo.models import (
    TelemetryEnvelope,
    YarboCommandResult,
    YarboLightState,
    YarboPlan,
    YarboRobot,
    YarboSchedule,
    YarboTelemetry,
)


class TestYarboLightState:
    def test_all_on(self):
        state = YarboLightState.all_on()
        assert state.led_head == 255
        assert state.led_left_w == 255
        assert state.body_left_r == 255
        assert state.tail_right_r == 255

    def test_all_off(self):
        state = YarboLightState.all_off()
        for val in state.to_dict().values():
            assert val == 0

    def test_to_dict_keys(self):
        state = YarboLightState(led_head=100, led_right_w=50)
        d = state.to_dict()
        expected_keys = {
            "led_head",
            "led_left_w",
            "led_right_w",
            "body_left_r",
            "body_right_r",
            "tail_left_r",
            "tail_right_r",
        }
        assert set(d.keys()) == expected_keys

    def test_from_dict(self, sample_light_on):
        state = YarboLightState.from_dict(sample_light_on)
        assert state.led_head == 255
        assert state.body_right_r == 255

    def test_partial_from_dict(self):
        """Missing keys default to 0."""
        state = YarboLightState.from_dict({"led_head": 200})
        assert state.led_head == 200
        assert state.led_left_w == 0

    def test_to_dict_values_are_ints(self):
        state = YarboLightState.all_on()
        for val in state.to_dict().values():
            assert isinstance(val, int)


class TestYarboRobot:
    def test_from_dict_basic(self):
        robot = YarboRobot.from_dict(
            {
                "sn": "YBG2412345",
                "name": "My Yarbo",
                "firmware": "3.11.0",
                "isOnline": True,
            }
        )
        assert robot.sn == "YBG2412345"
        assert robot.name == "My Yarbo"
        assert robot.firmware == "3.11.0"
        assert robot.is_online is True

    def test_from_dict_alt_keys(self):
        """Handles alternate field names (serialNum, robotName, etc.)."""
        robot = YarboRobot.from_dict(
            {
                "serialNum": "ABC123",
                "snowbotName": "Snow Beast",
            }
        )
        assert robot.sn == "ABC123"
        assert robot.name == "Snow Beast"

    def test_from_dict_empty(self):
        robot = YarboRobot.from_dict({})
        assert robot.sn == ""
        assert robot.is_online is False

    def test_raw_preserved(self):
        d = {"sn": "X1", "extra_field": "preserved"}
        robot = YarboRobot.from_dict(d)
        assert robot.raw["extra_field"] == "preserved"


class TestYarboTelemetry:
    def test_nested_device_msg(self, sample_telemetry_dict):
        """Primary path: nested DeviceMSG format from live protocol."""
        t = YarboTelemetry.from_dict(sample_telemetry_dict)
        assert t.battery == 83  # BatteryMSG.capacity
        assert t.working_state == 1  # StateMSG.working_state
        assert t.state == "active"  # derived from working_state
        assert t.charging_status == 2  # StateMSG.charging_status
        assert t.error_code == 0  # StateMSG.error_code
        assert t.heading == pytest.approx(339.4576)  # RTKMSG.heading
        assert t.position_x == pytest.approx(1.268)  # CombinedOdom.x
        assert t.position_y == pytest.approx(-0.338)  # CombinedOdom.y
        assert t.phi == pytest.approx(-0.359)  # CombinedOdom.phi
        assert t.led == 69666
        assert t.raw == sample_telemetry_dict

    def test_flat_legacy_compat(self, sample_telemetry_dict_flat):
        """Backward-compat path: flat keys still parse correctly."""
        t = YarboTelemetry.from_dict(sample_telemetry_dict_flat)
        assert t.sn == "24400102L8HO5227"
        assert t.battery == 85
        assert t.state == "idle"
        assert t.led == 69666
        assert t.position_x == pytest.approx(12.34)
        assert t.heading == pytest.approx(270.0)

    def test_missing_fields_are_none(self):
        t = YarboTelemetry.from_dict({"sn": "X1"})
        assert t.battery is None
        assert t.state is None
        assert t.position_x is None

    def test_flat_alt_battery_key(self):
        t = YarboTelemetry.from_dict({"bat": 42})
        assert t.battery == 42

    def test_flat_error_code(self):
        t = YarboTelemetry.from_dict({"errorCode": "E001"})
        assert t.error_code == "E001"

    def test_raw_preserved(self, sample_telemetry_dict):
        t = YarboTelemetry.from_dict(sample_telemetry_dict)
        assert t.raw is sample_telemetry_dict


class TestTelemetryEnvelope:
    def test_is_telemetry(self):
        e = TelemetryEnvelope(kind="DeviceMSG", payload={"BatteryMSG": {"capacity": 80}})
        assert e.is_telemetry is True
        assert e.is_heartbeat is False

    def test_is_heartbeat(self):
        e = TelemetryEnvelope(kind="heart_beat", payload={"working_state": 0})
        assert e.is_heartbeat is True
        assert e.is_telemetry is False

    def test_to_telemetry(self):
        payload = {"BatteryMSG": {"capacity": 90}, "StateMSG": {"working_state": 0}}
        e = TelemetryEnvelope(kind="DeviceMSG", payload=payload)
        t = e.to_telemetry()
        assert isinstance(t, YarboTelemetry)
        assert t.battery == 90


class TestYarboPlan:
    def test_from_dict_basic(self):
        plan = YarboPlan.from_dict({"planId": "p1", "planName": "Zone A"})
        assert plan.plan_id == "p1"
        assert plan.plan_name == "Zone A"

    def test_from_dict_with_params(self):
        plan = YarboPlan.from_dict(
            {
                "planId": "p1",
                "planName": "Front Yard",
                "areaId": "area-1",
                "params": {
                    "routeAngle": 45,
                    "routeSpacing": 0.3,
                    "speed": 0.8,
                    "perimeterLaps": 2,
                    "doubleCleaning": False,
                    "edgePriority": True,
                    "turningMode": "u-turn",
                },
            }
        )
        assert plan.plan_id == "p1"
        assert plan.area_id == "area-1"
        assert plan.params is not None
        assert plan.params.route_angle == 45
        assert plan.params.speed == pytest.approx(0.8)
        assert plan.params.edge_priority is True

    def test_empty(self):
        plan = YarboPlan.from_dict({})
        assert plan.plan_id == ""
        assert plan.area_ids == []
        assert plan.params is None


class TestYarboSchedule:
    def test_from_dict(self):
        sched = YarboSchedule.from_dict(
            {
                "scheduleId": "s1",
                "planId": "p1",
                "enabled": True,
                "scheduleType": "weekly",
                "weekdays": [1, 3, 5],
                "startTime": "07:00",
                "timezone": "America/New_York",
            }
        )
        assert sched.schedule_id == "s1"
        assert sched.plan_id == "p1"
        assert sched.enabled is True
        assert sched.schedule_type == "weekly"
        assert sched.weekdays == [1, 3, 5]
        assert sched.start_time == "07:00"
        assert sched.timezone == "America/New_York"

    def test_defaults(self):
        sched = YarboSchedule.from_dict({})
        assert sched.schedule_id == ""
        assert sched.enabled is True
        assert sched.weekdays == []


class TestYarboCommandResult:
    def test_success_state_zero(self):
        """state=0 → success."""
        result = YarboCommandResult.from_dict(
            {
                "topic": "get_controller",
                "state": 0,
                "data": {"controller": True},
            }
        )
        assert result.success is True
        assert result.topic == "get_controller"
        assert result.data == {"controller": True}

    def test_failure_nonzero_state(self):
        """Non-zero state → not success."""
        result = YarboCommandResult.from_dict(
            {
                "topic": "light_ctrl",
                "state": 1,
                "data": {},
            }
        )
        assert result.success is False
        assert result.state == 1

    def test_raw_preserved(self):
        d = {"topic": "cmd_buzzer", "state": 0, "data": {}, "extra": "val"}
        result = YarboCommandResult.from_dict(d)
        assert result.raw["extra"] == "val"

    def test_defaults(self):
        result = YarboCommandResult.from_dict({})
        assert result.topic == ""
        assert result.state == 0
        assert result.success is True
