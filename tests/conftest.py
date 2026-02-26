"""
pytest fixtures and mock MQTT broker for python-yarbo tests.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch
import zlib

import pytest

# ---------------------------------------------------------------------------
# Codec helpers (used in multiple test modules)
# ---------------------------------------------------------------------------


def zlib_encode(payload: dict) -> bytes:
    """Encode a dict to zlib-compressed JSON — matches the wire format."""
    return zlib.compress(json.dumps(payload, separators=(",", ":")).encode())


def zlib_decode(data: bytes) -> dict:
    """Decode zlib-compressed JSON bytes to dict."""
    return json.loads(zlib.decompress(data))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_sn() -> str:
    """A test robot serial number."""
    return "24400102L8HO5227"


@pytest.fixture
def sample_broker() -> str:
    """Test broker IP."""
    return "192.168.1.24"


@pytest.fixture
def sample_telemetry_dict() -> dict[str, Any]:
    """
    Realistic nested DeviceMSG telemetry payload (from live capture, 2026-02-24).

    Matches the confirmed live schema in MQTT_PROTOCOL.md.
    """
    return {
        "BatteryMSG": {
            "capacity": 83,
            "status": 3,
            "temp_err": 0,
            "timestamp": 1771943280.057,
        },
        "StateMSG": {
            "working_state": 1,
            "charging_status": 2,
            "error_code": 0,
            "machine_controller": 1,
        },
        "RTKMSG": {
            "heading": 339.4576,
            "status": "4",
            "timestamp": 1771943280.131,
        },
        "CombinedOdom": {
            "x": 1.268,
            "y": -0.338,
            "phi": -0.359,
        },
        "led": 69666,
        "timestamp": 1771943280.0,
    }


@pytest.fixture
def sample_telemetry_dict_flat() -> dict[str, Any]:
    """Legacy flat telemetry payload for backward-compat tests."""
    return {
        "sn": "24400102L8HO5227",
        "state": "idle",
        "battery": 85,
        "led": 69666,
        "posX": 12.34,
        "posY": -5.67,
        "heading": 270.0,
        "speed": 0.0,
        "errorCode": None,
    }


@pytest.fixture
def sample_light_on() -> dict[str, int]:
    """All-on light payload."""
    return {
        "led_head": 255,
        "led_left_w": 255,
        "led_right_w": 255,
        "body_left_r": 255,
        "body_right_r": 255,
        "tail_left_r": 255,
        "tail_right_r": 255,
    }


@pytest.fixture
def sample_light_off() -> dict[str, int]:
    """All-off light payload."""
    keys = [
        "led_head",
        "led_left_w",
        "led_right_w",
        "body_left_r",
        "body_right_r",
        "tail_left_r",
        "tail_right_r",
    ]
    return dict.fromkeys(keys, 0)


@pytest.fixture
def mock_paho_client():
    """
    Return a MagicMock that pretends to be a paho-mqtt Client.

    Sets up ``on_connect`` / ``on_message`` callbacks and auto-fires
    the connect callback when ``connect()`` is called.
    """
    with patch("paho.mqtt.client.Client") as MockClient:  # noqa: N806
        mock_instance = MagicMock()
        MockClient.return_value = mock_instance

        # Simulate successful connection — pass rc=0 matching paho v2 signature
        # (client, userdata, flags, reason_code, props)
        def connect_side_effect(host, port, **kwargs):
            # Fire the on_connect callback synchronously in test
            if mock_instance.on_connect:
                mock_instance.on_connect(mock_instance, None, None, 0, None)

        mock_instance.connect.side_effect = connect_side_effect
        mock_instance.loop_start = MagicMock()
        mock_instance.loop_stop = MagicMock()
        mock_instance.disconnect = MagicMock()
        mock_instance.subscribe = MagicMock()
        mock_instance.publish = MagicMock()

        yield mock_instance
