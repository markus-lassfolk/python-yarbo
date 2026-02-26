"""
yarbo._codec — zlib encode/decode helpers for MQTT payloads.

All MQTT payloads in the Yarbo protocol are zlib-compressed JSON.
The robot firmware checks the firmware version (>= 3.9.0, MIN_ZIP_MQTT_VERSION)
before decompressing. All current firmware versions use zlib compression.

Reference: Blutter ASM analysis of the Flutter app's MqttPublish class.
"""

from __future__ import annotations

import json
import zlib
from typing import Any


def encode(payload: dict[str, Any]) -> bytes:
    """
    Encode a Python dict to a zlib-compressed JSON byte string.

    This is the wire format for ALL MQTT publishes to the Yarbo broker.

    Args:
        payload: Dict to encode (must be JSON-serialisable).

    Returns:
        Compressed bytes ready to publish.

    Example::

        from yarbo._codec import encode, decode
        raw = encode({"led_head": 255, "led_left_w": 255})
        assert decode(raw) == {"led_head": 255, "led_left_w": 255}
    """
    return zlib.compress(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def decode(data: bytes) -> dict[str, Any]:
    """
    Decode a zlib-compressed JSON byte string to a Python dict.

    Falls back to plain JSON if decompression fails (for compatibility
    with any firmware that might not compress).

    Args:
        data: Raw bytes received from the MQTT broker.

    Returns:
        Decoded dict. Returns ``{"_raw": data.hex()}`` on total failure.
    """
    try:
        return json.loads(zlib.decompress(data))
    except zlib.error:
        pass
    try:
        return json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"_raw": data[:512].hex()}
