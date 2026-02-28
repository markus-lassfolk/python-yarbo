"""Tests for yarbo.error_reporting — _scrub_event, init_error_reporting, report_mqtt_dump_to_glitchtip."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from yarbo.error_reporting import (
    _scrub_event,
    _scrub_dict,
    _scrub_mqtt_envelope,
    init_error_reporting,
    report_mqtt_dump_to_glitchtip,
)


def _make_event(**kwargs):
    """Build a minimal Sentry event dict."""
    base = {"extra": {}, "breadcrumbs": {"values": []}}
    base.update(kwargs)
    return base


class TestScrubEvent:
    def test_extra_password_redacted(self):
        event = _make_event(extra={"password": "secret123", "user_id": "42"})
        result = _scrub_event(event, {})
        assert result["extra"]["password"] == "[REDACTED]"
        assert result["extra"]["user_id"] == "42"

    def test_extra_token_redacted(self):
        event = _make_event(extra={"auth_token": "tok_abc", "level": "info"})
        result = _scrub_event(event, {})
        assert result["extra"]["auth_token"] == "[REDACTED]"
        assert result["extra"]["level"] == "info"

    def test_extra_generic_key_redacted(self):
        event = _make_event(extra={"api_key": "xyz", "name": "bot"})
        result = _scrub_event(event, {})
        assert result["extra"]["api_key"] == "[REDACTED]"
        assert result["extra"]["name"] == "bot"

    def test_breadcrumb_message_with_password_redacted(self):
        event = _make_event(
            breadcrumbs={"values": [{"message": "Connecting with password abc123"}]}
        )
        result = _scrub_event(event, {})
        assert result["breadcrumbs"]["values"][0]["message"] == "[REDACTED]"

    def test_breadcrumb_message_without_sensitive_not_redacted(self):
        event = _make_event(
            breadcrumbs={"values": [{"message": "Connection established successfully"}]}
        )
        result = _scrub_event(event, {})
        msg = result["breadcrumbs"]["values"][0]["message"]
        assert msg == "Connection established successfully"

    def test_breadcrumb_data_key_redacted(self):
        event = _make_event(
            breadcrumbs={
                "values": [
                    {
                        "message": "auth attempt",
                        "data": {"password": "s3cr3t", "host": "broker.example.com"},
                    }
                ]
            }
        )
        result = _scrub_event(event, {})
        data = result["breadcrumbs"]["values"][0]["data"]
        assert data["password"] == "[REDACTED]"
        assert data["host"] == "broker.example.com"

    def test_breadcrumb_apikey_pattern_redacted(self):
        """The _SCRUB_KEY_PATTERN should catch 'apikey' in breadcrumb messages."""
        event = _make_event(breadcrumbs={"values": [{"message": "using apikey XYZ to connect"}]})
        result = _scrub_event(event, {})
        assert result["breadcrumbs"]["values"][0]["message"] == "[REDACTED]"

    def test_no_extra_or_breadcrumbs(self):
        """Events without extra/breadcrumbs should pass through unchanged."""
        event: dict = {}
        result = _scrub_event(event, {})
        assert result == {}


class TestInitErrorReporting:
    def test_disabled_enabled_false(self):
        """init_error_reporting should be a no-op when enabled=False."""
        # Should not raise and should not import sentry
        init_error_reporting(enabled=False)

    def test_disabled_empty_env_var(self, monkeypatch):
        """init_error_reporting should be a no-op when YARBO_SENTRY_DSN=''."""
        monkeypatch.setenv("YARBO_SENTRY_DSN", "")
        init_error_reporting()  # must not raise

    def test_no_dsn_no_env_is_noop(self, monkeypatch):
        """With no DSN argument and no env var, init should be a no-op."""
        monkeypatch.delenv("YARBO_SENTRY_DSN", raising=False)
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        init_error_reporting()  # must not raise


class TestScrubMqttEnvelope:
    def test_scrub_dict_redacts_sensitive_keys(self):
        d = {"password": "secret", "user": "alice", "token": "xyz"}
        assert _scrub_dict(d) == {"password": "[REDACTED]", "user": "alice", "token": "[REDACTED]"}

    def test_scrub_dict_nested(self):
        d = {"a": {"password": "p"}, "b": 1}
        assert _scrub_dict(d) == {"a": {"password": "[REDACTED]"}, "b": 1}

    def test_scrub_mqtt_envelope_scrubs_payload(self):
        env = {"direction": "received", "topic": "snowbot/SN/device/DeviceMSG", "payload": {"password": "x", "battery": 80}}
        out = _scrub_mqtt_envelope(env)
        assert out["payload"]["password"] == "[REDACTED]"
        assert out["payload"]["battery"] == 80


class TestReportMqttDumpToGlitchtip:
    def test_returns_false_when_sentry_not_initialized(self):
        """When Sentry is not initialized, report_mqtt_dump_to_glitchtip returns False."""
        result = report_mqtt_dump_to_glitchtip([{"direction": "sent", "topic": "t", "payload": {}}])
        assert result is False

    def test_calls_capture_message_when_sentry_initialized(self):
        """When Sentry is initialized, report_mqtt_dump_to_glitchtip sends dump via capture_message."""
        messages = [
            {"direction": "sent", "topic": "snowbot/SN/app/get_controller", "payload": {}},
            {"direction": "received", "topic": "snowbot/SN/device/DeviceMSG", "payload": {"battery": 80}},
        ]
        mock_sentry = MagicMock()
        mock_sentry.is_initialized.return_value = True
        old_sentry = sys.modules.get("sentry_sdk")
        sys.modules["sentry_sdk"] = mock_sentry
        try:
            result = report_mqtt_dump_to_glitchtip(messages)
        finally:
            if old_sentry is None:
                sys.modules.pop("sentry_sdk", None)
            else:
                sys.modules["sentry_sdk"] = old_sentry
        assert result is True
        mock_sentry.capture_message.assert_called_once()
        call_args = mock_sentry.capture_message.call_args
        assert call_args[0][0] == "MQTT dump (user-reported)"
        assert call_args[1]["level"] == "info"
        extras = call_args[1]["extras"]
        assert "mqtt_dump" in extras
        assert "message_count" in extras
        assert extras["message_count"] == 2
        assert "get_controller" in extras["mqtt_dump"]
        assert "DeviceMSG" in extras["mqtt_dump"]
        assert "80" in extras["mqtt_dump"]

    def test_scrubs_sensitive_payload_before_send(self):
        """Payload keys like password are redacted in the dump sent to GlitchTip."""
        messages = [
            {"direction": "received", "topic": "t", "payload": {"password": "secret", "battery": 50}},
        ]
        mock_sentry = MagicMock()
        mock_sentry.is_initialized.return_value = True
        old_sentry = sys.modules.get("sentry_sdk")
        sys.modules["sentry_sdk"] = mock_sentry
        try:
            report_mqtt_dump_to_glitchtip(messages)
        finally:
            if old_sentry is None:
                sys.modules.pop("sentry_sdk", None)
            else:
                sys.modules["sentry_sdk"] = old_sentry
        extras = mock_sentry.capture_message.call_args[1]["extras"]
        assert "[REDACTED]" in extras["mqtt_dump"]
        assert "secret" not in extras["mqtt_dump"]
        assert "50" in extras["mqtt_dump"]
