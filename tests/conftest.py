"""
pytest fixtures and mock MQTT broker for python-yarbo tests.
"""

from __future__ import annotations

import asyncio
import json
import zlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
    """Realistic DeviceMSG telemetry payload (from live capture)."""
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
    return {k: 0 for k in [
        "led_head", "led_left_w", "led_right_w",
        "body_left_r", "body_right_r", "tail_left_r", "tail_right_r",
    ]}


@pytest.fixture
def mock_paho_client():
    """
    Return a MagicMock that pretends to be a paho-mqtt Client.

    Sets up ``on_connect`` / ``on_message`` callbacks and auto-fires
    the connect callback when ``connect()`` is called.
    """
    with patch("paho.mqtt.client.Client") as MockClient:
        mock_instance = MagicMock()
        MockClient.return_value = mock_instance

        # Simulate successful connection
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
