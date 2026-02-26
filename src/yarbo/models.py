"""
yarbo.models — Typed dataclasses for every Yarbo protocol object.

All dataclasses use Python's ``dataclasses`` module and include ``from_dict``
factory methods for deserialising API / MQTT payloads.

References:
- docs/COMMAND_CATALOGUE.md — full MQTT command catalogue
- docs/LIGHT_CTRL_PROTOCOL.md — LED channel names and values
- docs/MQTT_PROTOCOL.md — DeviceMSG schema (live-confirmed 2026-02-24)
- Live API responses captured 2026-02-25
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Lights
# ---------------------------------------------------------------------------


@dataclass
class YarboLightState:
    """
    State of all 7 LED channels on the robot.

    Values range from 0 (off) to 255 (full brightness).
    Integer values only — booleans are NOT accepted by the firmware.

    Channels:
        led_head:     Front/head white light
        led_left_w:   Left fill light (white)
        led_right_w:  Right fill light (white)
        body_left_r:  Left body accent light (red)
        body_right_r: Right body accent light (red)
        tail_left_r:  Left tail/rear light (red)
        tail_right_r: Right tail/rear light (red)
    """

    led_head: int = 0
    led_left_w: int = 0
    led_right_w: int = 0
    body_left_r: int = 0
    body_right_r: int = 0
    tail_left_r: int = 0
    tail_right_r: int = 0

    def to_dict(self) -> dict[str, int]:
        """Return a dict suitable for the ``light_ctrl`` MQTT payload."""
        return {
            "led_head": self.led_head,
            "led_left_w": self.led_left_w,
            "led_right_w": self.led_right_w,
            "body_left_r": self.body_left_r,
            "body_right_r": self.body_right_r,
            "tail_left_r": self.tail_left_r,
            "tail_right_r": self.tail_right_r,
        }

    @classmethod
    def all_on(cls) -> YarboLightState:
        """Create a state with all channels at full brightness (255)."""
        return cls(255, 255, 255, 255, 255, 255, 255)

    @classmethod
    def all_off(cls) -> YarboLightState:
        """Create a state with all channels off (0)."""
        return cls(0, 0, 0, 0, 0, 0, 0)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> YarboLightState:
        return cls(
            led_head=d.get("led_head", 0),
            led_left_w=d.get("led_left_w", 0),
            led_right_w=d.get("led_right_w", 0),
            body_left_r=d.get("body_left_r", 0),
            body_right_r=d.get("body_right_r", 0),
            tail_left_r=d.get("tail_left_r", 0),
            tail_right_r=d.get("tail_right_r", 0),
        )


# ---------------------------------------------------------------------------
# Robot
# ---------------------------------------------------------------------------


@dataclass
class YarboRobot:
    """
    Metadata for a Yarbo robot device.

    Populated from the cloud REST API (``getUserRobotBindVos``) or from
    MQTT ``deviceinfo_feedback`` messages.
    """

    sn: str = ""
    """Robot serial number (e.g. ``"24400102L8HO5227"``)."""

    name: str = ""
    """User-assigned display name."""

    model: str = ""
    """Hardware model string (e.g. ``"Yarbo G1"``)."""

    firmware: str = ""
    """Firmware version string (e.g. ``"3.11.0"``)."""

    is_online: bool = False
    """Whether the robot is currently online (cloud API)."""

    bind_time: str | None = None
    """ISO timestamp when the robot was bound to this account."""

    broker_host: str = ""
    """
    Local MQTT broker IP discovered at runtime (populated by
    ``YarboLocalClient`` after connecting).
    """

    raw: dict[str, Any] = field(default_factory=dict)
    """Raw API response dict (for debugging)."""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> YarboRobot:
        return cls(
            sn=d.get("sn", d.get("serialNum", "")),
            name=d.get("name", d.get("robotName", d.get("snowbotName", ""))),
            model=d.get("model", d.get("robotModel", "")),
            firmware=d.get("firmware", d.get("firmwareVersion", "")),
            is_online=bool(d.get("isOnline", d.get("online", False))),
            bind_time=d.get("bindTime"),
            raw=d,
        )


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


@dataclass
class YarboTelemetry:
    """
    Parsed telemetry from ``DeviceMSG`` MQTT messages (~1-2 Hz).

    The ``DeviceMSG`` topic delivers full robot state with a nested schema.
    Field parsing handles the live-confirmed nested ``DeviceMSG`` format
    (``BatteryMSG.capacity``, ``StateMSG.working_state``, ``RTKMSG.heading``,
    ``CombinedOdom.x/y/phi``) as well as legacy flat payloads for
    backward compatibility.

    Not all fields are present in every message; absent fields default to
    ``None`` so callers can distinguish "not reported" from zero.

    Live protocol schema reference: ``yarbo-reversing/docs/MQTT_PROTOCOL.md``
    """

    sn: str = ""
    """Robot serial number."""

    battery: int | None = None
    """Battery state of charge (0-100 %). Source: ``BatteryMSG.capacity``."""

    state: str | None = None
    """
    Current operating state string.

    Derived from ``StateMSG.working_state``:
    - ``"idle"``    — working_state = 0
    - ``"active"``  — working_state = 1
    """

    working_state: int | None = None
    """Raw working state integer from ``StateMSG.working_state`` (0=idle, 1=active)."""

    charging_status: int | None = None
    """Charging status from ``StateMSG.charging_status`` (2 = charging/docked)."""

    error_code: int | str | None = None
    """Active fault code from ``StateMSG.error_code``, or ``None`` if no fault."""

    position_x: float | None = None
    """Local odometry X coordinate (metres). Source: ``CombinedOdom.x``."""

    position_y: float | None = None
    """Local odometry Y coordinate (metres). Source: ``CombinedOdom.y``."""

    phi: float | None = None
    """Local odometry heading (radians). Source: ``CombinedOdom.phi``."""

    heading: float | None = None
    """RTK compass heading in degrees (0-360). Source: ``RTKMSG.heading``."""

    speed: float | None = None
    """Current travel speed (m/s)."""

    led: int | None = None
    """
    Raw LED hardware register value.

    This is a hardware status bitmask, NOT the controllable LED state.
    Observed values:
    - 69666  (0x11022) — ambient standby lighting
    - 350207 (0x557FF) — all channels at 255

    .. note:: The live protocol delivers this as a string (e.g. ``"69666"``);
        ``from_dict`` coerces it to ``int`` automatically.
    """

    raw: dict[str, Any] = field(default_factory=dict)
    """Complete raw DeviceMSG dict."""

    @classmethod
    def from_dict(cls, d: dict[str, Any], topic: str | None = None) -> YarboTelemetry:
        """
        Parse a DeviceMSG dict into a YarboTelemetry instance.

        Handles both the live nested DeviceMSG format and legacy flat payloads.

        Args:
            d:     Decoded DeviceMSG payload dict.
            topic: Optional full MQTT topic string (e.g.
                   ``"snowbot/24400102L8HO5227/device/DeviceMSG"``).
                   Used to extract the robot serial number when the payload's
                   ``sn`` field is absent (which is common in live captures).
        """
        # Nested DeviceMSG sub-messages (live protocol format)
        battery_msg: dict[str, Any] = d.get("BatteryMSG", {}) or {}
        state_msg: dict[str, Any] = d.get("StateMSG", {}) or {}
        rtk_msg: dict[str, Any] = d.get("RTKMSG", {}) or {}
        odom: dict[str, Any] = d.get("CombinedOdom", {}) or {}

        # Battery: nested first, flat fallback
        battery: int | None
        battery = battery_msg.get("capacity") if battery_msg else d.get("battery", d.get("bat"))

        # Working state: nested first, flat fallback
        working_state: int | None = state_msg.get("working_state") if state_msg else None
        if working_state is not None:
            state: str | None = "active" if working_state else "idle"
        else:
            state = d.get("state", d.get("workState"))

        # Error code: nested first, flat fallback
        error_code: int | str | None
        if state_msg:
            error_code = state_msg.get("error_code", d.get("errorCode", d.get("err")))
        else:
            error_code = d.get("errorCode", d.get("err"))

        # Position: CombinedOdom first, flat fallback
        position_x: float | None = odom.get("x") if odom else d.get("posX", d.get("x"))
        position_y: float | None = odom.get("y") if odom else d.get("posY", d.get("y"))
        phi: float | None = odom.get("phi") if odom else d.get("phi")

        # Heading: RTKMSG first, flat fallback
        heading: float | None = (
            rtk_msg.get("heading") if rtk_msg else d.get("heading", d.get("yaw"))
        )

        # Derive SN: payload field first, then extract from MQTT topic.
        # Topic format: snowbot/{SN}/device/{feedback}
        sn: str = d.get("sn", "") or ""
        if not sn and topic:
            parts = topic.split("/")
            if len(parts) >= 2:
                sn = parts[1]

        # Coerce led: live protocol delivers it as a string (e.g. "69666").
        # Guard against non-numeric firmware values (e.g. "", "off") by falling
        # back to None rather than crashing the entire telemetry parsing path.
        raw_led = d.get("led")
        led: int | None
        if raw_led is None:
            led = None
        else:
            try:
                led = int(raw_led)
            except (ValueError, TypeError):
                led = None

        return cls(
            sn=sn,
            battery=battery,
            state=state,
            working_state=working_state,
            charging_status=state_msg.get("charging_status") if state_msg else None,
            error_code=error_code,
            position_x=position_x,
            position_y=position_y,
            phi=phi,
            heading=heading,
            speed=d.get("speed"),
            led=led,
            raw=d,
        )


# ---------------------------------------------------------------------------
# Telemetry envelope
# ---------------------------------------------------------------------------


@dataclass
class TelemetryEnvelope:
    """
    Envelope wrapping a raw MQTT message from the robot with its topic context.

    Yielded by :meth:`~yarbo.mqtt.MqttTransport.telemetry_stream` so that
    callers can differentiate message kinds (``DeviceMSG``, ``plan_feedback``,
    ``heart_beat``, etc.) without discarding non-telemetry messages.

    Example::

        async for envelope in transport.telemetry_stream():
            if envelope.kind == "DeviceMSG":
                telemetry = YarboTelemetry.from_dict(envelope.payload)
            elif envelope.kind == "heart_beat":
                working = envelope.payload.get("working_state")
    """

    kind: str
    """Topic leaf name (e.g. ``"DeviceMSG"``, ``"heart_beat"``, ``"data_feedback"``)."""

    payload: dict[str, Any]
    """Decoded message payload dict."""

    topic: str = ""
    """Full MQTT topic string (e.g. ``"snowbot/SN/device/DeviceMSG"``)."""

    @property
    def is_telemetry(self) -> bool:
        """True if this is a full DeviceMSG telemetry payload."""
        return self.kind == "DeviceMSG"

    @property
    def is_heartbeat(self) -> bool:
        """True if this is a heart_beat payload."""
        return self.kind == "heart_beat"

    def to_telemetry(self) -> YarboTelemetry:
        """Parse the payload as a :class:`YarboTelemetry` instance.

        Passes ``self.topic`` so that the SN can be derived from the MQTT
        topic when the payload's ``sn`` field is absent.
        """
        return YarboTelemetry.from_dict(self.payload, topic=self.topic)


# ---------------------------------------------------------------------------
# Plans and Schedules
# ---------------------------------------------------------------------------


@dataclass
class YarboPlanParams:
    """Route and execution parameters for a work plan."""

    route_angle: float | None = None
    """Route angle in degrees."""

    route_spacing: float | None = None
    """Row spacing in metres."""

    speed: float | None = None
    """Travel speed in m/s."""

    perimeter_laps: int | None = None
    """Number of perimeter passes."""

    double_cleaning: bool = False
    """Whether to make a second cleaning pass."""

    edge_priority: bool = False
    """Whether to prioritise edge cleaning."""

    obstacle_avoidance: str = "standard"
    """Obstacle avoidance mode."""

    turning_mode: str = "u-turn"
    """Turning strategy at row ends."""

    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> YarboPlanParams:
        return cls(
            route_angle=d.get("routeAngle"),
            route_spacing=d.get("routeSpacing"),
            speed=d.get("speed"),
            perimeter_laps=d.get("perimeterLaps"),
            double_cleaning=bool(d.get("doubleCleaning", False)),
            edge_priority=bool(d.get("edgePriority", False)),
            obstacle_avoidance=d.get("obstacleAvoidance", "standard"),
            turning_mode=d.get("turningMode", "u-turn"),
            raw=d,
        )


@dataclass
class YarboPlan:
    """A saved work plan (zone, path, and settings).

    Schema matches ``MQTT_PROTOCOL.md`` plan schema (inferred from protocol
    analysis and live captures).
    """

    plan_id: str = ""
    """Plan UUID."""

    plan_name: str = ""
    """Display name shown in the app."""

    area_id: str = ""
    """Associated clean-area UUID."""

    area_ids: list[str] = field(default_factory=list)
    """List of area UUIDs (for multi-area plans)."""

    params: YarboPlanParams | None = None
    """Route and execution parameters."""

    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> YarboPlan:
        params_dict: dict[str, Any] = d.get("params", {}) or {}
        return cls(
            plan_id=d.get("planId", d.get("id", "")),
            plan_name=d.get("planName", d.get("name", "")),
            area_id=d.get("areaId", ""),
            area_ids=d.get("areaIds", []),
            params=YarboPlanParams.from_dict(params_dict) if params_dict else None,
            raw=d,
        )


@dataclass
class YarboSchedule:
    """A time-based schedule that triggers a work plan automatically.

    Schema matches ``MQTT_PROTOCOL.md`` schedule schema (inferred from
    protocol analysis).
    """

    schedule_id: str = ""
    """Schedule UUID."""

    plan_id: str = ""
    """UUID of the plan to execute."""

    enabled: bool = True
    """Whether the schedule is active."""

    schedule_type: str = "weekly"
    """Schedule recurrence type (``"weekly"`` is most common)."""

    weekdays: list[int] = field(default_factory=list)
    """Days of the week to run (ISO weekday: 1=Mon … 7=Sun)."""

    start_time: str = ""
    """Start time as ``"HH:MM"`` (local time)."""

    timezone: str = ""
    """IANA timezone identifier (e.g. ``"America/New_York"``)."""

    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> YarboSchedule:
        return cls(
            schedule_id=d.get("scheduleId", d.get("id", "")),
            plan_id=d.get("planId", ""),
            enabled=bool(d.get("enabled", True)),
            schedule_type=d.get("scheduleType", "weekly"),
            weekdays=d.get("weekdays", []),
            start_time=d.get("startTime", ""),
            timezone=d.get("timezone", ""),
            raw=d,
        )


# ---------------------------------------------------------------------------
# Command result
# ---------------------------------------------------------------------------


@dataclass
class YarboCommandResult:
    """
    Response envelope for MQTT command feedback messages.

    Commands published to ``snowbot/{sn}/app/{cmd}`` generate a response
    on ``snowbot/{sn}/device/data_feedback`` in the format::

        {"topic": "<cmd>", "state": 0, "data": {...}}

    where ``state == 0`` indicates success.
    """

    topic: str = ""
    """Echo of the command name (e.g. ``"get_controller"``, ``"light_ctrl"``)."""

    state: int = 0
    """Result code: ``0`` = success, non-zero = failure."""

    data: dict[str, Any] = field(default_factory=dict)
    """Command-specific response payload."""

    raw: dict[str, Any] = field(default_factory=dict)
    """Complete raw response dict."""

    @property
    def success(self) -> bool:
        """True if ``state == 0``."""
        return self.state == 0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> YarboCommandResult:
        state_val = d.get("state") or 0
        try:
            state = int(state_val)
        except (ValueError, TypeError):
            state = 0
        return cls(
            topic=d.get("topic", ""),
            state=state,
            data=d.get("data", {}),
            raw=d,
        )
