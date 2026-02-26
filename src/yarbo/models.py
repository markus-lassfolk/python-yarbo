"""
yarbo.models — Typed dataclasses for every Yarbo protocol object.

All dataclasses use Python's ``dataclasses`` module and include ``from_dict``
factory methods for deserialising API / MQTT payloads.

References:
- docs/COMMAND_CATALOGUE.md — full MQTT command catalogue
- docs/LIGHT_CTRL_PROTOCOL.md — LED channel names and values
- Live API responses captured 2026-02-25
"""

from __future__ import annotations

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
    def all_on(cls) -> "YarboLightState":
        """Create a state with all channels at full brightness (255)."""
        return cls(255, 255, 255, 255, 255, 255, 255)

    @classmethod
    def all_off(cls) -> "YarboLightState":
        """Create a state with all channels off (0)."""
        return cls(0, 0, 0, 0, 0, 0, 0)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "YarboLightState":
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
    def from_dict(cls, d: dict[str, Any]) -> "YarboRobot":
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
    Parsed telemetry from ``data_feedback`` MQTT messages (DeviceMSG).

    The ``data_feedback`` topic delivers robot state at ~1 Hz.
    Field names are inferred from packet captures and Dart source analysis.

    Not all fields are present in every message; absent fields default to
    ``None`` so callers can distinguish "not reported" from zero.
    """

    sn: str = ""
    """Robot serial number."""

    battery: int | None = None
    """Battery state of charge (0–100 %)."""

    state: str | None = None
    """Current operating state string (e.g. ``"idle"``, ``"working"``, ``"charging"``)."""

    error_code: str | None = None
    """Active fault code, or ``None`` if no fault."""

    position_x: float | None = None
    """RTK X coordinate (metres, local frame)."""

    position_y: float | None = None
    """RTK Y coordinate (metres, local frame)."""

    heading: float | None = None
    """Robot heading in degrees (0–360, north-up)."""

    speed: float | None = None
    """Current travel speed (m/s)."""

    led: int | None = None
    """
    Raw LED hardware register value.

    This is a hardware status bitmask, NOT the controllable LED state.
    Observed values:
    - 69666  (0x11022) — ambient standby lighting
    - 350207 (0x557FF) — all channels at 255
    """

    raw: dict[str, Any] = field(default_factory=dict)
    """Complete raw DeviceMSG dict."""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "YarboTelemetry":
        return cls(
            sn=d.get("sn", ""),
            battery=d.get("battery", d.get("bat")),
            state=d.get("state", d.get("workState")),
            error_code=d.get("errorCode", d.get("err")),
            position_x=d.get("posX", d.get("x")),
            position_y=d.get("posY", d.get("y")),
            heading=d.get("heading", d.get("yaw")),
            speed=d.get("speed"),
            led=d.get("led"),
            raw=d,
        )


# ---------------------------------------------------------------------------
# Plans and Schedules
# ---------------------------------------------------------------------------


@dataclass
class YarboPlan:
    """A saved work plan (zone, path, and settings)."""

    plan_id: str = ""
    name: str = ""
    area_ids: list[str] = field(default_factory=list)
    speed: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "YarboPlan":
        return cls(
            plan_id=d.get("planId", d.get("id", "")),
            name=d.get("name", ""),
            area_ids=d.get("areaIds", []),
            speed=d.get("speed"),
            raw=d,
        )


@dataclass
class YarboSchedule:
    """A time-based schedule that triggers a work plan automatically."""

    schedule_id: str = ""
    plan_id: str = ""
    enabled: bool = True
    cron: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "YarboSchedule":
        return cls(
            schedule_id=d.get("scheduleId", d.get("id", "")),
            plan_id=d.get("planId", ""),
            enabled=bool(d.get("enabled", True)),
            cron=d.get("cron", ""),
            raw=d,
        )


# ---------------------------------------------------------------------------
# Command result
# ---------------------------------------------------------------------------


@dataclass
class YarboCommandResult:
    """
    Response envelope for MQTT commands that return a feedback message.

    Commands published to ``snowbot/{sn}/app/{cmd}`` may generate
    a response on ``snowbot/{sn}/device/data_feedback``.
    """

    success: bool = True
    msg_type: str = ""
    code: str = ""
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "YarboCommandResult":
        return cls(
            success=bool(d.get("success", True)),
            msg_type=d.get("type", ""),
            code=d.get("code", ""),
            message=d.get("message", ""),
            data=d.get("data", {}),
        )
