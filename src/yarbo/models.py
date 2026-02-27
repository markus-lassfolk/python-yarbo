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

from dataclasses import dataclass, field
import enum
from typing import Any


def flatten_mqtt_payload(payload: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """
    Flatten a nested MQTT payload into dotted keys so every value is visible.

    Example: ``{"BatteryMSG": {"capacity": 100}}`` -> ``{"BatteryMSG.capacity": 100}``.
    Lists are indexed: ``{"x": [1, 2]}`` -> ``{"x.0": 1, "x.1": 2}``.
    """
    out: dict[str, Any] = {}
    for k, v in payload.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict) and v:
            out.update(flatten_mqtt_payload(v, key))
        elif isinstance(v, list):
            if not v:
                out[key] = v
            else:
                for i, item in enumerate(v):
                    # Recurse into non-empty dicts; store empty dicts and scalars directly
                    # so that {} inside lists is emitted as key.<i>={} rather than dropped.
                    if isinstance(item, dict) and item:
                        out.update(flatten_mqtt_payload(item, f"{key}.{i}"))
                    else:
                        out[f"{key}.{i}"] = item
        else:
            out[key] = v
    return out


# Dotted MQTT keys that we map into the structured status table (YarboTelemetry.from_dict).
# Used by scripts/compare_mqtt_log.py to report "missing from structured table".
STRUCTURED_MQTT_KEYS: frozenset[str] = frozenset({
    "BatteryMSG.capacity", "BatteryMSG.status", "BatteryMSG.temp_err",
    "BatteryMSG.timestamp", "BatteryMSG.wireless_charge_voltage",
    "BatteryMSG.wireless_charge_current",
    "StateMSG.working_state", "StateMSG.charging_status", "StateMSG.error_code",
    "StateMSG.machine_controller", "StateMSG.on_going_planning",
    "StateMSG.on_going_recharging", "StateMSG.planning_paused",
    "StateMSG.car_controller", "StateMSG.chute_angle", "StateMSG.route_priority",
    "StateMSG.adjustangle_status", "StateMSG.auto_draw_waiting_state",
    "StateMSG.en_state_led", "StateMSG.en_warn_led",
    "StateMSG.on_going_to_start_point", "StateMSG.on_mul_points",
    "StateMSG.robot_follow_state", "StateMSG.schedule_cancel",
    "StateMSG.vision_auto_draw_state",
    "RTKMSG.heading", "RTKMSG.status", "RTKMSG.timestamp",
    "RTKMSG.gga_atn_dis", "RTKMSG.heading_atn_dis", "RTKMSG.heading_dop",
    "RTKMSG.heading_status", "RTKMSG.pre4_timestamp", "RTKMSG.rtk_version",
    "CombinedOdom.x", "CombinedOdom.y", "CombinedOdom.phi",
    "CombinedOdom.confidence", "combined_odom_confidence",
    "HeadMsg.head_type", "HeadMsg.name", "HeadMsg.sn", "HeadMsg.serial_number",
    "HeadSerialMsg.head_sn", "RunningStatusMSG.chute_angle",
    "RunningStatusMSG.chute_steering_engine_info",
    "RunningStatusMSG.elec_navigation_front_right_sensor",
    "RunningStatusMSG.elec_navigation_rear_right_sensor",
    "RunningStatusMSG.head_gyro_pitch", "RunningStatusMSG.head_gyro_roll",
    "RunningStatusMSG.rain_sensor_data",
    "BodyMsg.recharge_state",
    "rtk_base_data.rover.gngga", "rtk_base_data.base.gngga",
    "rtk_base_data.rover.heading",
    "route_priority.hg0", "route_priority.wlan0", "route_priority.wwan0",
    "sn", "battery", "bat", "state", "workState", "errorCode", "err",
    "posX", "x", "posY", "y", "phi", "heading", "yaw", "speed", "led", "name",
    "timestamp", "route_priority", "head_sn", "battery_status",
    "wireless_charge_voltage", "wireless_charge_current",
    "wireless_recharge.state", "wireless_recharge.error_code",
    "base_status", "bds", "bs", "ms", "s", "sbs", "tms",
    "green_grass_update_switch", "ipcamera_ota_switch", "rtcm_age",
    "rtcm_info.current_source_type",
    "ultrasonic_msg.lf_dis", "ultrasonic_msg.mt_dis", "ultrasonic_msg.rf_dis",
    "EletricMSG.push_pod_current",
})


# ---------------------------------------------------------------------------
# Head type enum
# ---------------------------------------------------------------------------


class HeadType(enum.IntEnum):
    """Attachment head type as reported in ``HeadMsg.head_type``."""

    Snow = 0
    """Snow blower head."""

    Mower = 1
    """Standard mower head."""

    MowerPro = 2
    """Pro mower head."""

    Leaf = 3
    """Leaf blower head."""

    SAM = 4
    """SAM head."""

    Trimmer = 5
    """Trimmer head."""

    NoHead = 6
    """No head attached."""


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
# NMEA GNGGA parsing helper
# ---------------------------------------------------------------------------


def _parse_gngga(  # noqa: PLR0912
    sentence: str,
) -> tuple[float | None, float | None, float | None, int]:
    """Parse a GNGGA NMEA 0183 sentence into GPS coordinates.

    NOTE: Latitude/Longitude are intentionally exposed as core features for location queries.

    Format::

        $GNGGA,time,lat,N/S,lon,E/W,quality,sats,hdop,alt,M,...*checksum

    Args:
        sentence: Raw GNGGA sentence string (with or without checksum).

    Returns:
        Tuple of ``(latitude, longitude, altitude, fix_quality)`` where
        latitude and longitude are in decimal degrees (positive = N/E) and
        altitude is in metres above MSL.  Latitude/longitude/altitude are
        ``None`` when fix quality is 0 (invalid) or fields are absent.
        ``fix_quality`` is always an int (0 = invalid).
    """
    if not sentence.startswith(("$GNGGA", "$GPGGA")):
        return None, None, None, 0
    # Strip NMEA checksum (*XX) if present
    if "*" in sentence:
        sentence = sentence[: sentence.index("*")]
    parts = sentence.split(",")
    if len(parts) < 10:
        return None, None, None, 0

    try:
        fix_quality = int(parts[6]) if parts[6] else 0
    except ValueError:
        fix_quality = 0

    if fix_quality == 0:
        return None, None, None, 0

    # Latitude: DDMM.MMMM → decimal degrees
    lat: float | None = None
    if parts[2] and parts[3]:
        try:
            raw_lat = parts[2]
            lat_deg = float(raw_lat[:2])
            lat_min = float(raw_lat[2:])
            lat = lat_deg + lat_min / 60.0
            if parts[3].upper() == "S":
                lat = -lat
        except (ValueError, IndexError):
            lat = None

    # Longitude: DDDMM.MMMM → decimal degrees
    lon: float | None = None
    if parts[4] and parts[5]:
        try:
            raw_lon = parts[4]
            lon_deg = float(raw_lon[:3])
            lon_min = float(raw_lon[3:])
            lon = lon_deg + lon_min / 60.0
            if parts[5].upper() == "W":
                lon = -lon
        except (ValueError, IndexError):
            lon = None

    # Altitude in metres (field 9)
    alt: float | None = None
    if len(parts) > 9 and parts[9]:
        try:
            alt = float(parts[9])
        except ValueError:
            alt = None

    return lat, lon, alt, fix_quality


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

    head_type: int | None = None
    """Attachment head type integer. See :class:`HeadType` enum. Source: ``HeadMsg.head_type``."""

    on_going_planning: bool | None = None
    """True while a work plan is actively executing. Source: ``StateMSG.on_going_planning``."""

    on_going_recharging: bool | None = None
    """True while returning to or docking at the base. Source: ``StateMSG.on_going_recharging``."""

    planning_paused: bool | None = None
    """True when the active plan has been paused. Source: ``StateMSG.planning_paused``."""

    machine_controller: int | None = None
    """Active controller identifier. Source: ``StateMSG.machine_controller``."""

    # Extra fields (align with PowerShell / app display)
    name: str | None = None
    """Robot display name."""

    head_serial_number: str | None = None
    """Attachment head serial number. Source: ``HeadMsg.sn`` or similar."""

    battery_status: int | None = None
    """Battery status from ``BatteryMSG.status`` (e.g. charge state)."""

    battery_temp_err: int | None = None
    """Battery temperature error flag from ``BatteryMSG.temp_err`` (0 = OK)."""

    rtk_status: str | None = None
    """RTK status string. Source: ``RTKMSG.status`` (e.g. ``"4"`` = fixed)."""

    chute_angle: int | float | None = None
    """Chute angle/position (snow blower)."""

    odom_confidence: int | float | None = None
    """Odometry confidence. Source: ``CombinedOdom.confidence`` or similar."""

    car_controller: bool | None = None
    """True if app/car has controller role. Source: ``StateMSG.car_controller`` or similar."""

    wireless_charge_voltage: float | None = None
    """Wireless charging voltage (when docked)."""

    wireless_charge_current: float | None = None
    """Wireless charging current (when docked)."""

    wireless_recharge_state: int | None = None
    """Wireless recharge state from ``wireless_recharge.state`` (0 = OK)."""

    wireless_recharge_error_code: int | None = None
    """Wireless recharge error from ``wireless_recharge.error_code``."""

    route_priority: str | int | None = None
    """Route priority or current route info."""

    last_updated: float | None = None
    """Payload timestamp (seconds since epoch).

    Source: ``timestamp`` or ``BatteryMSG.timestamp``.
    """

    # Plan feedback fields (merged from plan_feedback topic)
    plan_id: str | None = None
    """Active plan UUID. Populated from ``plan_feedback`` messages."""

    plan_state: str | None = None
    """Current plan execution state (e.g. ``"running"``, ``"paused"``). From ``plan_feedback``."""

    area_covered: float | None = None
    """Area covered so far in the active plan (m²). From ``plan_feedback``."""

    duration: float | None = None
    """Elapsed plan duration in seconds. From ``plan_feedback``."""

    # GPS fields (parsed from rtk_base_data.rover.gngga NMEA sentence)
    # NOTE: Latitude/Longitude are intentionally exposed as core features for location queries
    latitude: float | None = None
    """GPS latitude in decimal degrees (WGS84, positive=N). Source: ``gngga``."""

    longitude: float | None = None
    """GPS longitude in decimal degrees (WGS84, positive=E). Source: ``gngga``."""

    altitude: float | None = None
    """GPS altitude in metres above MSL. Source: ``rtk_base_data.rover.gngga``."""

    fix_quality: int = 0
    """GNSS fix quality (GNGGA field 6). 0=invalid, 1=GPS, 2=DGPS, 4=RTK fixed, 5=RTK float."""

    # Extended MQTT fields (all payload keys in structured table)
    body_recharge_state: int | None = None
    """Body recharge state from ``BodyMsg.recharge_state``."""
    rtk_gga_atn_dis: float | None = None
    rtk_heading_atn_dis: float | None = None
    rtk_heading_dop: float | None = None
    rtk_heading_status: int | None = None
    rtk_pre4_timestamp: float | None = None
    rtk_version: str | None = None
    chute_steering_engine_info: int | None = None
    elec_navigation_front_right_sensor: int | float | None = None
    elec_navigation_rear_right_sensor: int | float | None = None
    head_gyro_pitch: float | None = None
    head_gyro_roll: float | None = None
    rain_sensor_data: int | float | None = None
    adjustangle_status: int | None = None
    auto_draw_waiting_state: int | None = None
    en_state_led: bool | None = None
    en_warn_led: bool | None = None
    on_going_to_start_point: int | None = None
    on_mul_points: int | None = None
    robot_follow_state: bool | None = None
    schedule_cancel: int | None = None
    vision_auto_draw_state: int | None = None
    base_status: int | None = None
    bds: int | None = None
    bs: int | None = None
    ms: int | None = None
    s: int | None = None
    sbs: int | None = None
    tms: int | None = None
    green_grass_update_switch: int | None = None
    ipcamera_ota_switch: int | None = None
    rtcm_age: float | None = None
    rtcm_current_source_type: int | None = None
    rtk_base_gngga: str | None = None
    rtk_rover_heading: str | None = None
    ultrasonic_lf_dis: int | float | None = None
    ultrasonic_mt_dis: int | float | None = None
    ultrasonic_rf_dis: int | float | None = None
    push_pod_current: int | float | None = None
    """From ``EletricMSG.push_pod_current``."""

    raw: dict[str, Any] = field(default_factory=dict)
    """Complete raw DeviceMSG dict."""

    def all_mqtt_values(self) -> dict[str, Any]:
        """
        Return every MQTT payload key-value as a flat dict with dotted keys.

        Use this to see or iterate over every value the device sent, including
        keys we do not yet parse into named fields.
        """
        return flatten_mqtt_payload(self.raw)

    @property
    def head_name(self) -> str:
        """Human-readable head type name derived from :attr:`head_type`.

        Returns ``"Unknown"`` when ``head_type`` is ``None``, or
        ``"Unknown(<value>)"`` for unrecognised integers.
        """
        if self.head_type is None:
            return "Unknown"
        try:
            return HeadType(self.head_type).name
        except ValueError:
            return f"Unknown({self.head_type})"

    @property
    def battery_capacity(self) -> int | None:
        """Alias for :attr:`battery` (battery state of charge, 0-100 %).

        Both names refer to the same underlying value; ``battery_capacity``
        is the more descriptive form preferred in new code.
        """
        return self.battery

    @property
    def serial_number(self) -> str:
        """Alias for :attr:`sn` (robot serial number).

        ``serial_number`` is the more descriptive form preferred in new code.
        """
        return self.sn

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
        head_msg: dict[str, Any] = d.get("HeadMsg", {}) or {}
        head_serial_msg: dict[str, Any] = d.get("HeadSerialMsg", {}) or {}
        running_status: dict[str, Any] = d.get("RunningStatusMSG", {}) or {}
        wireless_recharge: dict[str, Any] = d.get("wireless_recharge", {}) or {}
        body_msg: dict[str, Any] = d.get("BodyMsg", {}) or {}
        eletric_msg: dict[str, Any] = d.get("EletricMSG", {}) or {}
        ultrasonic_msg: dict[str, Any] = d.get("ultrasonic_msg", {}) or {}
        rtcm_info: dict[str, Any] = d.get("rtcm_info", {}) or {}
        rtk_base_data: dict[str, Any] = d.get("rtk_base_data", {}) or {}
        rover: dict[str, Any] = rtk_base_data.get("rover", {}) or {}
        rtk_base: dict[str, Any] = rtk_base_data.get("base", {}) or {}

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

        # Activity state fields from StateMSG
        def _optional_bool(v: object) -> bool | None:
            return bool(v) if v is not None else None

        def _str_or_none(v: object) -> str | None:
            return str(v) if v is not None else None

        on_going_planning: bool | None = (
            _optional_bool(state_msg.get("on_going_planning")) if state_msg else None
        )
        on_going_recharging: bool | None = (
            _optional_bool(state_msg.get("on_going_recharging")) if state_msg else None
        )
        planning_paused: bool | None = (
            _optional_bool(state_msg.get("planning_paused")) if state_msg else None
        )

        # GPS: parse GNGGA NMEA sentence from rtk_base_data.rover.gngga
        gngga_sentence: str = rover.get("gngga", "") or ""
        gps_lat, gps_lon, gps_alt, gps_fix = (
            _parse_gngga(gngga_sentence) if gngga_sentence else (None, None, None, 0)
        )

        # Timestamp: top-level or from BatteryMSG/RTKMSG
        last_updated: float | None = d.get("timestamp")
        if last_updated is None and battery_msg:
            last_updated = battery_msg.get("timestamp")
        if last_updated is None and rtk_msg:
            last_updated = rtk_msg.get("timestamp")

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
            head_type=head_msg.get("head_type") if head_msg else None,
            on_going_planning=on_going_planning,
            on_going_recharging=on_going_recharging,
            planning_paused=planning_paused,
            machine_controller=state_msg.get("machine_controller") if state_msg else None,
            name=d.get("name") or (head_msg.get("name") if head_msg else None),
            head_serial_number=(
                (head_msg.get("sn") or head_msg.get("serial_number")) if head_msg else None
            )
            or head_serial_msg.get("head_sn")
            or d.get("head_sn"),
            battery_status=battery_msg.get("status") if battery_msg else d.get("battery_status"),
            battery_temp_err=battery_msg.get("temp_err") if battery_msg else d.get("battery_temp_err"),
            rtk_status=_str_or_none(rtk_msg.get("status") if rtk_msg else d.get("rtk_status")),
            chute_angle=(
                running_status.get("chute_angle")
                or (state_msg.get("chute_angle") if state_msg else None)
                or d.get("chute_angle")
            ),
            odom_confidence=(
                odom.get("confidence")
                if odom
                else d.get("combined_odom_confidence", d.get("odom_confidence"))
            ),
            car_controller=_optional_bool(
                state_msg.get("car_controller") if state_msg else d.get("car_controller")
            ),
            wireless_charge_voltage=battery_msg.get("wireless_charge_voltage")
            if battery_msg
            else d.get("wireless_charge_voltage"),
            wireless_charge_current=battery_msg.get("wireless_charge_current")
            if battery_msg
            else d.get("wireless_charge_current"),
            wireless_recharge_state=wireless_recharge.get("state"),
            wireless_recharge_error_code=wireless_recharge.get("error_code"),
            route_priority=d.get("route_priority")
            if d.get("route_priority") is not None
            else (state_msg.get("route_priority") if state_msg else None),
            last_updated=last_updated,
            latitude=gps_lat,
            longitude=gps_lon,
            altitude=gps_alt,
            fix_quality=gps_fix,
            body_recharge_state=body_msg.get("recharge_state"),
            rtk_gga_atn_dis=rtk_msg.get("gga_atn_dis") if rtk_msg else None,
            rtk_heading_atn_dis=rtk_msg.get("heading_atn_dis") if rtk_msg else None,
            rtk_heading_dop=rtk_msg.get("heading_dop") if rtk_msg else None,
            rtk_heading_status=rtk_msg.get("heading_status") if rtk_msg else None,
            rtk_pre4_timestamp=rtk_msg.get("pre4_timestamp") if rtk_msg else None,
            rtk_version=_str_or_none(rtk_msg.get("rtk_version")) if rtk_msg else None,
            chute_steering_engine_info=running_status.get("chute_steering_engine_info"),
            elec_navigation_front_right_sensor=running_status.get(
                "elec_navigation_front_right_sensor"
            ),
            elec_navigation_rear_right_sensor=running_status.get(
                "elec_navigation_rear_right_sensor"
            ),
            head_gyro_pitch=running_status.get("head_gyro_pitch"),
            head_gyro_roll=running_status.get("head_gyro_roll"),
            rain_sensor_data=running_status.get("rain_sensor_data"),
            adjustangle_status=state_msg.get("adjustangle_status") if state_msg else None,
            auto_draw_waiting_state=state_msg.get("auto_draw_waiting_state")
            if state_msg
            else None,
            en_state_led=_optional_bool(state_msg.get("en_state_led")) if state_msg else None,
            en_warn_led=_optional_bool(state_msg.get("en_warn_led")) if state_msg else None,
            on_going_to_start_point=state_msg.get("on_going_to_start_point")
            if state_msg
            else None,
            on_mul_points=state_msg.get("on_mul_points") if state_msg else None,
            robot_follow_state=_optional_bool(state_msg.get("robot_follow_state"))
            if state_msg
            else None,
            schedule_cancel=state_msg.get("schedule_cancel") if state_msg else None,
            vision_auto_draw_state=state_msg.get("vision_auto_draw_state")
            if state_msg
            else None,
            base_status=d.get("base_status"),
            bds=d.get("bds"),
            bs=d.get("bs"),
            ms=d.get("ms"),
            s=d.get("s"),
            sbs=d.get("sbs"),
            tms=d.get("tms"),
            green_grass_update_switch=d.get("green_grass_update_switch"),
            ipcamera_ota_switch=d.get("ipcamera_ota_switch"),
            rtcm_age=d.get("rtcm_age"),
            rtcm_current_source_type=rtcm_info.get("current_source_type"),
            rtk_base_gngga=rtk_base.get("gngga") if rtk_base else None,
            rtk_rover_heading=rover.get("heading") if rover else None,
            ultrasonic_lf_dis=ultrasonic_msg.get("lf_dis"),
            ultrasonic_mt_dis=ultrasonic_msg.get("mt_dis"),
            ultrasonic_rf_dis=ultrasonic_msg.get("rf_dis"),
            push_pod_current=eletric_msg.get("push_pod_current"),
            raw=d,
        )

    @classmethod
    def from_plan_feedback(cls, d: dict[str, Any]) -> YarboTelemetry:
        """Parse a ``plan_feedback`` MQTT message into a partial :class:`YarboTelemetry`.

        Only the plan-specific fields are populated; all robot-state fields
        (battery, position, etc.) are left at their defaults (``None``/``""``)
        and should be merged with data from a ``DeviceMSG`` message.

        Args:
            d: Decoded ``plan_feedback`` payload dict.

        Returns:
            :class:`YarboTelemetry` with plan tracking fields populated.
        """
        return cls(
            plan_id=d.get("planId"),
            plan_state=d.get("state"),
            area_covered=d.get("areaCovered"),
            duration=d.get("duration"),
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

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the MQTT ``save_schedule`` payload format."""
        return {
            "scheduleId": self.schedule_id,
            "planId": self.plan_id,
            "enabled": self.enabled,
            "scheduleType": self.schedule_type,
            "weekdays": self.weekdays,
            "startTime": self.start_time,
            "timezone": self.timezone,
        }


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
        if "state" in d:
            state_val = d["state"]
            if state_val is None:
                state = 0
            else:
                try:
                    state = int(state_val)
                except (ValueError, TypeError):
                    # Unparseable state: treat as failure sentinel; raw value in raw.
                    state = -1
        else:
            state = 0
        return cls(
            topic=d.get("topic", ""),
            state=state,
            data=d.get("data", {}),
            raw=d,
        )
