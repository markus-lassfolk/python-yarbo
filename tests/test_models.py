"""Tests for yarbo.models — dataclass parsing and factories."""

from __future__ import annotations

import pytest

from yarbo.models import (
    HeadType,
    TelemetryEnvelope,
    YarboCommandResult,
    YarboLightState,
    YarboPlan,
    YarboRobot,
    YarboSchedule,
    YarboTelemetry,
    _parse_gngga,
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

    def test_head_type_from_head_msg(self):
        """HeadMsg.head_type is parsed into head_type field."""
        d = {"HeadMsg": {"head_type": 1}}
        t = YarboTelemetry.from_dict(d)
        assert t.head_type == 1
        assert t.head_name == "Mower"

    def test_head_type_none_when_missing(self):
        t = YarboTelemetry.from_dict({"sn": "X1"})
        assert t.head_type is None
        assert t.head_name == "Unknown"

    def test_head_name_all_enum_values(self):
        for ht in HeadType:
            t = YarboTelemetry.from_dict({"HeadMsg": {"head_type": int(ht)}})
            assert t.head_name == ht.name

    def test_head_name_unknown_value(self):
        t = YarboTelemetry.from_dict({"HeadMsg": {"head_type": 99}})
        assert t.head_name == "Unknown(99)"

    def test_activity_state_fields_from_state_msg(self):
        """on_going_planning, on_going_recharging, planning_paused, machine_controller parsed."""
        d = {
            "StateMSG": {
                "working_state": 1,
                "charging_status": 0,
                "error_code": 0,
                "on_going_planning": True,
                "on_going_recharging": False,
                "planning_paused": False,
                "machine_controller": 2,
            }
        }
        t = YarboTelemetry.from_dict(d)
        assert t.on_going_planning is True
        assert t.on_going_recharging is False
        assert t.planning_paused is False
        assert t.machine_controller == 2

    def test_activity_state_none_when_missing(self):
        t = YarboTelemetry.from_dict({"sn": "X1"})
        assert t.on_going_planning is None
        assert t.on_going_recharging is None
        assert t.planning_paused is None
        assert t.machine_controller is None

    def test_machine_controller_in_fixture(self, sample_telemetry_dict):
        """machine_controller=1 in the live fixture is parsed correctly."""
        t = YarboTelemetry.from_dict(sample_telemetry_dict)
        assert t.machine_controller == 1


class TestYarboTelemetryAliases:
    """Tests for battery_capacity and serial_number aliases (Issue #16)."""

    def test_battery_capacity_alias(self, sample_telemetry_dict):
        t = YarboTelemetry.from_dict(sample_telemetry_dict)
        assert t.battery_capacity == t.battery
        assert t.battery_capacity == 83

    def test_battery_capacity_none(self):
        t = YarboTelemetry.from_dict({})
        assert t.battery_capacity is None

    def test_serial_number_alias(self):
        t = YarboTelemetry.from_dict({}, topic="snowbot/24400102L8HO5227/device/DeviceMSG")
        assert t.serial_number == "24400102L8HO5227"
        assert t.serial_number == t.sn

    def test_serial_number_from_payload(self):
        t = YarboTelemetry.from_dict({"sn": "MYSN"})
        assert t.serial_number == "MYSN"


class TestYarboTelemetryPlanFeedback:
    def test_from_plan_feedback_basic(self):
        d = {
            "planId": "plan-abc",
            "state": "running",
            "areaCovered": 120.5,
            "duration": 300.0,
        }
        t = YarboTelemetry.from_plan_feedback(d)
        assert t.plan_id == "plan-abc"
        assert t.plan_state == "running"
        assert t.area_covered == pytest.approx(120.5)
        assert t.duration == pytest.approx(300.0)
        # Non-plan fields default to None
        assert t.battery is None
        assert t.sn == ""

    def test_from_plan_feedback_missing_fields(self):
        t = YarboTelemetry.from_plan_feedback({})
        assert t.plan_id is None
        assert t.plan_state is None
        assert t.area_covered is None
        assert t.duration is None

    def test_plan_fields_none_in_device_msg(self):
        """DeviceMSG by itself has no plan tracking fields."""
        t = YarboTelemetry.from_dict({"BatteryMSG": {"capacity": 80}})
        assert t.plan_id is None
        assert t.plan_state is None


class TestHeadType:
    def test_enum_values(self):
        assert HeadType.Snow == 0
        assert HeadType.Mower == 1
        assert HeadType.MowerPro == 2
        assert HeadType.Leaf == 3
        assert HeadType.SAM == 4
        assert HeadType.Trimmer == 5
        assert HeadType.NoHead == 6

    def test_from_int(self):
        assert HeadType(0) is HeadType.Snow
        assert HeadType(6) is HeadType.NoHead


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


class TestParseGNGGA:
    """Tests for _parse_gngga NMEA sentence parser (Issue #18)."""

    # Real GNGGA samples
    SAMPLE_NORTH_EAST = "$GNGGA,123519,4807.038,N,01131.324,E,1,08,0.9,545.4,M,46.9,M,,*42"
    SAMPLE_SOUTH_WEST = "$GNGGA,194530,3352.905,S,07047.610,W,4,12,0.6,32.1,M,-17.0,M,,*52"
    SAMPLE_RTK_FIXED = "$GNGGA,095821,5321.574,N,00617.812,W,4,14,0.5,12.3,M,50.1,M,,*7A"
    SAMPLE_NO_FIX = "$GNGGA,000000,,,,,0,00,99.9,0.0,M,0.0,M,,*48"
    SAMPLE_NOT_GNGGA = "$GPGSV,3,1,12,..."

    def test_north_east_coordinates(self):
        lat, lon, alt, fix = _parse_gngga(self.SAMPLE_NORTH_EAST)
        assert lat is not None
        assert lon is not None
        assert lat == pytest.approx(48.0 + 7.038 / 60, rel=1e-5)
        assert lon == pytest.approx(11.0 + 31.324 / 60, rel=1e-5)
        assert alt == pytest.approx(545.4, rel=1e-5)
        assert fix == 1

    def test_south_west_coordinates(self):
        lat, lon, _alt, fix = _parse_gngga(self.SAMPLE_SOUTH_WEST)
        assert lat is not None and lat < 0  # South
        assert lon is not None and lon < 0  # West
        assert fix == 4  # RTK fixed

    def test_rtk_fixed_fix_quality(self):
        lat, lon, _alt, fix = _parse_gngga(self.SAMPLE_RTK_FIXED)
        assert fix == 4
        assert lat is not None
        assert lon is not None

    def test_no_fix_returns_none(self):
        lat, lon, _alt, fix = _parse_gngga(self.SAMPLE_NO_FIX)
        assert fix == 0
        assert lat is None
        assert lon is None

    def test_non_gngga_sentence(self):
        lat, lon, _alt, fix = _parse_gngga(self.SAMPLE_NOT_GNGGA)
        assert lat is None
        assert lon is None
        assert fix == 0

    def test_empty_string(self):
        lat, _lon, _alt, fix = _parse_gngga("")
        assert lat is None
        assert fix == 0


class TestYarboTelemetryGPS:
    """Tests for GPS fields in YarboTelemetry parsed from DeviceMSG (Issue #18)."""

    GNGGA = "$GNGGA,123519,4807.038,N,01131.324,E,1,08,0.9,545.4,M,46.9,M,,*42"

    def test_gps_fields_parsed_from_device_msg(self):
        d = {"rtk_base_data": {"rover": {"gngga": self.GNGGA}}}
        t = YarboTelemetry.from_dict(d)
        assert t.latitude is not None
        assert t.longitude is not None
        assert t.altitude == pytest.approx(545.4, rel=1e-5)
        assert t.fix_quality == 1

    def test_gps_none_when_no_rtk_data(self):
        t = YarboTelemetry.from_dict({"sn": "X1"})
        assert t.latitude is None
        assert t.longitude is None
        assert t.altitude is None
        assert t.fix_quality == 0

    def test_gps_none_when_no_fix(self):
        d = {"rtk_base_data": {"rover": {"gngga": "$GNGGA,000000,,,,,0,00,99.9,0.0,M,0.0,M,,*48"}}}
        t = YarboTelemetry.from_dict(d)
        assert t.latitude is None
        assert t.longitude is None
        assert t.fix_quality == 0
