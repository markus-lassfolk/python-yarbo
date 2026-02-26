"""Tests for yarbo._codec — zlib encode/decode helpers."""

from __future__ import annotations

import json
import zlib

from yarbo._codec import decode, encode


class TestEncode:
    def test_returns_bytes(self):
        result = encode({"hello": "world"})
        assert isinstance(result, bytes)

    def test_round_trip(self):
        payload = {"led_head": 255, "led_left_w": 128, "body_left_r": 0}
        assert decode(encode(payload)) == payload

    def test_zlib_compressed(self):
        raw = encode({"x": 1})
        # Should decompress without error
        decompressed = zlib.decompress(raw)
        assert json.loads(decompressed) == {"x": 1}

    def test_compact_json(self):
        """Encoder uses compact separators (',', ':') to save bytes."""
        raw = encode({"a": 1, "b": 2})
        decompressed = zlib.decompress(raw).decode()
        assert " " not in decompressed  # no spaces in keys/values

    def test_empty_dict(self):
        assert decode(encode({})) == {}

    def test_nested_dict(self):
        payload = {"data": {"nested": [1, 2, 3]}, "flag": True}
        assert decode(encode(payload)) == payload

    def test_unicode(self):
        payload = {"name": "Täst Röbot"}
        assert decode(encode(payload)) == payload


class TestDecode:
    def test_valid_zlib_json(self):
        payload = {"state": "idle", "battery": 85}
        encoded = zlib.compress(json.dumps(payload).encode())
        assert decode(encoded) == payload

    def test_fallback_plain_json(self):
        """Falls back to plain JSON if decompression fails."""
        payload = {"type": "data_feedback"}
        plain = json.dumps(payload).encode()
        assert decode(plain) == payload

    def test_garbage_returns_raw_hex(self):
        """Completely invalid data returns a ``_raw`` dict."""
        result = decode(b"\xff\xfe\xfd not valid")
        assert "_raw" in result

    def test_real_light_ctrl_payload(self, sample_light_on):
        encoded = encode(sample_light_on)
        decoded = decode(encoded)
        assert decoded == sample_light_on

    def test_buzzer_payload_round_trip(self):
        payload = {"state": 1, "timeStamp": 1700000000000}
        assert decode(encode(payload)) == payload

    def test_chute_payload_round_trip(self):
        assert decode(encode({"vel": 90})) == {"vel": 90}
