"""GlitchTip/Sentry error reporting for python-yarbo.

Provides init_error_reporting() for crash/error reporting and
report_mqtt_dump_to_glitchtip() to send captured MQTT traffic for
troubleshooting (e.g. firmware/configs maintainers cannot test locally).
Sensitive payload keys are scrubbed before send.
"""

import json
import logging
import os
import re
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Default DSN for the python-yarbo GlitchTip project.
# Opt-out: set YARBO_SENTRY_DSN="" to disable error reporting.
_DEFAULT_DSN = "https://c690590f8f664d609f6abe4cb0392d53@villapolly.duckdns.org/2"

# Default DSN for the python-yarbo GlitchTip project.
# Enabled by default during beta to help find issues.
# Opt-out: set YARBO_SENTRY_DSN="" or pass enabled=False.
_DEFAULT_DSN = "https://c690590f8f664d609f6abe4cb0392d53@glitchtip.lassfolk.cc/2"


def init_error_reporting(
    dsn: str | None = None,
    environment: str = "production",
    enabled: bool = True,
    tags: dict[str, str] | None = None,
) -> None:
    """Initialize Sentry/GlitchTip error reporting for python-yarbo.

    Enabled by default during beta with a built-in DSN. No PII is collected;
    credentials and sensitive keys are scrubbed before sending.

    To opt out, set ``YARBO_SENTRY_DSN=""`` or pass ``enabled=False``.

    Args:
        dsn: Sentry DSN. If omitted, falls back to the ``YARBO_SENTRY_DSN`` or
             ``SENTRY_DSN`` environment variables, then the built-in default.
        environment: Environment tag (production/development/testing).
        enabled: Master switch. If False, no SDK initialization occurs.
        tags: Optional extra tags (e.g. robot_serial, library_version).
    """
    if not enabled:
        return

    # Resolve DSN: explicit arg > YARBO_SENTRY_DSN env var > built-in default
    env_dsn = os.environ.get("YARBO_SENTRY_DSN")
    if env_dsn is not None and env_dsn == "":
        _LOGGER.debug('Error reporting explicitly disabled via YARBO_SENTRY_DSN=""')
        return
    effective_dsn = dsn or env_dsn or _DEFAULT_DSN

    dsn = dsn or env_dsn or os.environ.get("SENTRY_DSN") or _DEFAULT_DSN

    if not dsn:
        return

    try:
        import sentry_sdk  # noqa: PLC0415

        sentry_sdk.init(
            dsn=effective_dsn,
            environment=environment,
            traces_sample_rate=0.1,
            send_default_pii=False,
            before_send=_scrub_event,
        )

        if tags:
            for key, value in tags.items():
                sentry_sdk.set_tag(key, value)

        _LOGGER.debug("Error reporting initialized (dsn=%s...)", effective_dsn[:30])
    except ImportError:
        _LOGGER.debug("sentry-sdk not installed; error reporting disabled")
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Failed to initialize error reporting: %s", exc)


# Non-sensitive field names that contain "_key" but must not be redacted.
_KEY_ALLOWLIST: frozenset[str] = frozenset({"entity_key"})

# Pattern to detect key-like strings in breadcrumb messages (e.g., "apikey", "access_key")
_SCRUB_KEY_KEYWORDS: tuple[str, ...] = ("password", "token", "secret", "credential", "key")
_SCRUB_MSG_KEYWORDS: tuple[str, ...] = ("password", "token", "secret", "credential")
_SCRUB_KEY_PATTERN = re.compile(r"(?:_|api|access|auth|private)key", re.IGNORECASE)


def _is_sensitive_key(key_lower: str) -> bool:
    """Check if a key name looks sensitive and should be redacted."""
    if any(s in key_lower for s in ("password", "token", "secret", "credential")):
        return True
    if key_lower in _KEY_ALLOWLIST:
        return False
    return (
        key_lower == "key"
        or "_key" in key_lower
        or key_lower.startswith("key_")
        or key_lower.endswith("key")
    )


def _scrub_event(event: dict, hint: dict) -> dict:  # type: ignore[type-arg]
    """Remove sensitive data before sending."""
    if "extra" in event:
        for key in list(event["extra"]):
            if _is_sensitive_key(key.lower()):
                event["extra"][key] = "[REDACTED]"

    if "breadcrumbs" in event and "values" in event["breadcrumbs"]:
        for breadcrumb in event["breadcrumbs"]["values"]:
            if "message" in breadcrumb:
                msg = str(breadcrumb["message"])
                msg_lower = msg.lower()
                keywords = ("password", "token", "secret", "credential")
                sensitive = any(s in msg_lower for s in keywords)
                if sensitive or _SCRUB_KEY_PATTERN.search(msg):
                    breadcrumb["message"] = "[REDACTED]"
            if "data" in breadcrumb and isinstance(breadcrumb["data"], dict):
                for key in list(breadcrumb["data"]):
                    if _is_sensitive_key(key.lower()):
                        breadcrumb["data"][key] = "[REDACTED]"

    return event


def report_mqtt_dump_to_glitchtip(
    messages: list[dict[str, Any]],
    max_messages: int = 500,
    max_payload_chars: int = 50_000,
) -> bool:
    """Send a full MQTT dump to GlitchTip for troubleshooting/support.

    Use when reporting firmware or protocol issues so maintainers can inspect
    sent/received traffic. Payloads are scrubbed for sensitive keys before send.

    Args:
        messages: List of envelope dicts with "direction", "topic", "payload".
        max_messages: Cap number of messages to attach (default 500).
        max_payload_chars: Truncate total payload JSON if larger (default 50k).

    Returns:
        True if the dump was sent, False if Sentry is disabled or send failed.
    """
    try:
        import sentry_sdk  # noqa: PLC0415
    except ImportError:
        _LOGGER.debug("sentry-sdk not installed; MQTT dump not sent")
        return False

    if not sentry_sdk.is_initialized():
        _LOGGER.debug("Error reporting not initialized; MQTT dump not sent")
        return False

    trimmed = messages[-max_messages:] if len(messages) > max_messages else messages
    scrubbed = [_scrub_mqtt_envelope(m) for m in trimmed]
    dump = json.dumps(scrubbed, indent=2, ensure_ascii=False)
    if len(dump) > max_payload_chars:
        dump = dump[:max_payload_chars] + "\n... (truncated)"

    sentry_sdk.capture_message(
        "MQTT dump (user-reported)",
        level="info",
        extras={"mqtt_dump": dump, "message_count": len(scrubbed)},
    )
    _LOGGER.info("MQTT dump sent to GlitchTip (%d messages)", len(scrubbed))
    return True


def _scrub_mqtt_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the envelope with sensitive payload keys redacted."""
    out = dict(envelope)
    payload = out.get("payload")
    if isinstance(payload, dict):
        out["payload"] = _scrub_dict(payload)
    return out


def _scrub_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Recursively redact values for keys that look sensitive."""
    result = {}
    for k, v in d.items():
        if any(s in k.lower() for s in _SCRUB_KEY_KEYWORDS):
            result[k] = "[REDACTED]"
        elif isinstance(v, dict):
            result[k] = _scrub_dict(v)
        elif isinstance(v, list):
            result[k] = [_scrub_dict(x) if isinstance(x, dict) else x for x in v]
        else:
            result[k] = v
    return result
