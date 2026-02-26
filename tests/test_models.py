"""Tests for yarbo.models — dataclass parsing and factories."""

from __future__ import annotations

import pytest

from yarbo.models import (
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
            "led_head", "led_left_w", "led_right_w",
            "body_left_r", "body_right_r", "tail_left_r", "tail_right_r",
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
        robot = YarboRobot.from_dict({
            "sn": "YBG2412345",
            "name": "My Yarbo",
            "firmware": "3.11.0",
            "isOnline": True,
        })
        assert robot.sn == "YBG2412345"
        assert robot.name == "My Yarbo"
        assert robot.firmware == "3.11.0"
        assert robot.is_online is True

    def test_from_dict_alt_keys(self):
        """Handles alternate field names (serialNum, robotName, etc.)."""
        robot = YarboRobot.from_dict({
            "serialNum": "ABC123",
            "snowbotName": "Snow Beast",
        })
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
    def test_from_dict(self, sample_telemetry_dict):
        t = YarboTelemetry.from_dict(sample_telemetry_dict)
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

    def test_alt_battery_key(self):
        t = YarboTelemetry.from_dict({"bat": 42})
        assert t.battery == 42

    def test_error_code(self):
        t = YarboTelemetry.from_dict({"errorCode": "E001"})
        assert t.error_code == "E001"


class TestYarboPlan:
    def test_from_dict(self):
        plan = YarboPlan.from_dict({"planId": "p1", "name": "Zone A", "speed": 1.5})
        assert plan.plan_id == "p1"
        assert plan.name == "Zone A"
        assert plan.speed == 1.5

    def test_empty(self):
        plan = YarboPlan.from_dict({})
        assert plan.plan_id == ""
        assert plan.area_ids == []


class TestYarboSchedule:
    def test_from_dict(self):
        sched = YarboSchedule.from_dict({
            "scheduleId": "s1",
            "planId": "p1",
            "enabled": True,
            "cron": "0 8 * * 1-5",
        })
        assert sched.schedule_id == "s1"
        assert sched.enabled is True
        assert sched.cron == "0 8 * * 1-5"


class TestYarboCommandResult:
    def test_success(self):
        result = YarboCommandResult.from_dict({"success": True, "type": "light_ctrl"})
        assert result.success is True
        assert result.msg_type == "light_ctrl"

    def test_failure(self):
        result = YarboCommandResult.from_dict({
            "success": False,
            "code": "E001",
            "message": "Not controller",
        })
        assert result.success is False
        assert result.code == "E001"
