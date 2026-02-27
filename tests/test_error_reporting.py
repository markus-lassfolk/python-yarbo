"""Tests for yarbo.error_reporting — _scrub_event and init_error_reporting."""

from __future__ import annotations

from yarbo.error_reporting import _scrub_event, init_error_reporting


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
