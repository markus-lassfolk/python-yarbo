"""
yarbo.const — Protocol constants for the Yarbo MQTT interface.

All topic templates, port numbers, and default values used by the
local and cloud MQTT transports.

Discovery sources:
- Blutter ASM analysis of the Flutter app (libapp.so)
- Live packet captures on the local EMQX broker at 192.168.1.24 / 192.168.1.55
- yarbo-reversing/docs/COMMAND_CATALOGUE.md

Transport support matrix
------------------------
+---------------------------+-------------+--------------+
| Transport                 | Implemented | Notes        |
+===========================+=============+==============+
| Local MQTT (1883)         | ✅ Yes      | Primary      |
| Local WebSocket (8083)    | ❌ No       | TODO         |
| Local REST (8088)         | ❌ No       | TODO         |
| Local TCP JSON (22220)    | ❌ No       | TODO         |
| Cloud REST (HTTPS)        | ✅ Yes      | JWT auth     |
| Cloud MQTT (TLS/8883)     | ✅ Yes      | YarboCloudMqttClient |
+---------------------------+-------------+--------------+
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Local broker (same WiFi as robot)
# ---------------------------------------------------------------------------

#: Default local EMQX broker address (Yarbo's embedded broker, HaLow network).
LOCAL_BROKER_DEFAULT = "192.168.1.24"

#: Secondary local EMQX broker IP (also observed in the wild on HaLow networks).
LOCAL_BROKER_SECONDARY = "192.168.1.55"

#: Local broker plaintext port.
LOCAL_PORT = 1883

#: Local broker WebSocket port (not yet implemented in this library).
LOCAL_PORT_WS = 8083

# ---------------------------------------------------------------------------
# Cloud broker (Tencent TDMQ)
# ---------------------------------------------------------------------------

#: Tencent TDMQ MQTT broker hostname.
CLOUD_BROKER = "mqtt-b8rkj5da-usw-public.mqtt.tencenttdmq.com"

#: Cloud broker plaintext port.
CLOUD_PORT = 1883

#: Cloud broker TLS port.
CLOUD_PORT_TLS = 8883

# ---------------------------------------------------------------------------
# MQTT topic templates
# Topics follow the pattern: snowbot/{SN}/app/{cmd} or snowbot/{SN}/device/{type}
# ---------------------------------------------------------------------------

#: Template for publishing commands to the robot (app → robot).
TOPIC_APP_TMPL = "snowbot/{sn}/app/{cmd}"

#: Template for subscribing to robot feedback (robot → app).
TOPIC_DEVICE_TMPL = "snowbot/{sn}/device/{feedback}"

# Command leaf names (publish)
TOPIC_LEAF_GET_CONTROLLER = "get_controller"
TOPIC_LEAF_LIGHT_CTRL = "light_ctrl"
TOPIC_LEAF_CMD_BUZZER = "cmd_buzzer"
TOPIC_LEAF_CMD_CHUTE = "cmd_chute"

# Feedback leaf names (subscribe)
TOPIC_LEAF_DATA_FEEDBACK = "data_feedback"
TOPIC_LEAF_PLAN_FEEDBACK = "plan_feedback"
TOPIC_LEAF_RECHARGE_FEEDBACK = "recharge_feedback"
TOPIC_LEAF_OTA_FEEDBACK = "ota_feedback"
TOPIC_LEAF_PATROL_FEEDBACK = "patrol_feedback"
TOPIC_LEAF_CLOUD_POINTS = "cloud_points_feedback"
TOPIC_LEAF_DEVICE_INFO = "deviceinfo_feedback"
TOPIC_LEAF_LOG_FEEDBACK = "log_feedback"
TOPIC_LEAF_A_PROPERTY_1 = "a_property_1_feedback"

# Live-confirmed telemetry leaves (zlib JSON ~1-2 Hz)
TOPIC_LEAF_DEVICE_MSG = "DeviceMSG"
"""Full telemetry payload: BatteryMSG, StateMSG, RTKMSG, CombinedOdom, etc."""

# Live-confirmed heartbeat leaf (plain JSON ~1 Hz)
TOPIC_LEAF_HEART_BEAT = "heart_beat"
"""Heartbeat: plain JSON ``{"working_state": 0|1}``.
NOTE: ``heart_beat`` is NOT zlib-compressed — the codec's plain-JSON
fallback handles this transparently."""

#: All feedback topics to subscribe to (leaf names only — expand with TOPIC_DEVICE_TMPL).
ALL_FEEDBACK_LEAVES: list[str] = [
    TOPIC_LEAF_DEVICE_MSG,
    TOPIC_LEAF_HEART_BEAT,
    TOPIC_LEAF_DATA_FEEDBACK,
    TOPIC_LEAF_PLAN_FEEDBACK,
    TOPIC_LEAF_RECHARGE_FEEDBACK,
    TOPIC_LEAF_OTA_FEEDBACK,
    TOPIC_LEAF_PATROL_FEEDBACK,
    TOPIC_LEAF_CLOUD_POINTS,
    TOPIC_LEAF_DEVICE_INFO,
    TOPIC_LEAF_LOG_FEEDBACK,
    TOPIC_LEAF_A_PROPERTY_1,
]

# ---------------------------------------------------------------------------
# Light channel names
# ---------------------------------------------------------------------------

#: All 7 LED channel keys in the light_ctrl payload.
LIGHT_CHANNEL_KEYS: list[str] = [
    "led_head",
    "led_left_w",
    "led_right_w",
    "body_left_r",
    "body_right_r",
    "tail_left_r",
    "tail_right_r",
]

# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------

#: MQTT keepalive interval in seconds.
MQTT_KEEPALIVE = 60

#: Default timeout (seconds) waiting for a command response.
DEFAULT_CMD_TIMEOUT = 5.0

#: Default timeout (seconds) waiting for broker connection.
DEFAULT_CONNECT_TIMEOUT = 10.0

# ---------------------------------------------------------------------------
# Cloud REST API
# ---------------------------------------------------------------------------

#: Primary REST API gateway.
REST_BASE_URL = "https://4zx17x5q7l.execute-api.us-east-1.amazonaws.com/Stage"

#: MQTT policy key API gateway.
POLICY_KEY_BASE_URL = "https://ms0frm2hkf.execute-api.us-east-1.amazonaws.com/dev/app"


# ---------------------------------------------------------------------------
# Topic helper
# ---------------------------------------------------------------------------


class Topic:
    """
    Helper for building and decomposing Yarbo MQTT topic strings.

    All Yarbo topics follow the pattern::

        snowbot/{sn}/app/{cmd}       — app → robot (publish)
        snowbot/{sn}/device/{leaf}   — robot → app (subscribe)

    Example::

        t = Topic("24400102L8HO5227")
        t.app("light_ctrl")            # "snowbot/24400102L8HO5227/app/light_ctrl"
        t.device("data_feedback")      # "snowbot/24400102L8HO5227/device/data_feedback"
        Topic.parse("snowbot/SN/device/data_feedback")  # ("SN", "data_feedback")
    """

    def __init__(self, sn: str) -> None:
        self._sn = sn

    def app(self, cmd: str) -> str:
        """Build an app→robot publish topic."""
        return TOPIC_APP_TMPL.format(sn=self._sn, cmd=cmd)

    def device(self, feedback: str) -> str:
        """Build a robot→app subscribe topic."""
        return TOPIC_DEVICE_TMPL.format(sn=self._sn, feedback=feedback)

    @staticmethod
    def parse(topic: str) -> tuple[str, str]:
        """
        Extract ``(sn, leaf)`` from a full topic string.

        Returns ``("", "")`` if the topic doesn't match the expected pattern.
        """
        parts = topic.split("/")
        if len(parts) >= 4 and parts[0] == "snowbot":
            return parts[1], parts[3]
        return "", ""

    @staticmethod
    def leaf(topic: str) -> str:
        """Return the leaf (last) component of a topic string."""
        return topic.rsplit("/", 1)[-1]
