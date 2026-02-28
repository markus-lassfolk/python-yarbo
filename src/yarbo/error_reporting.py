"""GlitchTip/Sentry error reporting for python-yarbo.

Provides init_error_reporting() for crash/error reporting and
report_mqtt_dump_to_glitchtip() to send captured MQTT traffic for
troubleshooting (e.g. firmware/configs maintainers cannot test locally).
Sensitive payload keys are scrubbed before send.
"""

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Sensitive-key scrubbing helpers — compiled once at import time.
_SCRUB_KEY_KEYWORDS: tuple[str, ...] = ("password", "token", "secret", "credential", "key")
_SCRUB_MSG_KEYWORDS: tuple[str, ...] = ("password", "token", "secret", "credential")
_SCRUB_KEY_PATTERN = re.compile(r"(?:_|api|access|auth|private)key", re.IGNORECASE)


def init_error_reporting(
    dsn: str | None = None,
    environment: str = "production",
    enabled: bool = True,
) -> None:
    """Initialize Sentry/GlitchTip error reporting.

    Opt-in: enable by providing a DSN via argument or environment variables.
    To disable explicitly, pass enabled=False or set YARBO_SENTRY_DSN="".

    Args:
        dsn: Sentry DSN. If omitted, falls back to the ``YARBO_SENTRY_DSN`` or
             ``SENTRY_DSN`` environment variables.  No compiled-in default is
             provided; set the env var explicitly.  Pass ``enabled=False`` or
             set ``YARBO_SENTRY_DSN=""`` to fully disable reporting.
        environment: Environment tag (production/development/testing).
        enabled: Master switch. If False, no SDK initialization occurs.
    """
    if not enabled:
        return

    import os  # noqa: PLC0415

    # Check for explicit disable via empty env var
    env_dsn = os.environ.get("YARBO_SENTRY_DSN")
    if env_dsn is not None and env_dsn == "":
        return  # Explicitly disabled

    dsn = dsn or env_dsn or os.environ.get("SENTRY_DSN")

    if not dsn:
        return

    try:
        import sentry_sdk  # noqa: PLC0415

        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            traces_sample_rate=0.1,
            send_default_pii=False,
            before_send=_scrub_event,  # type: ignore[arg-type, unused-ignore]
        )
        logger.debug("Error reporting initialized (dsn=%s...)", dsn[:30])
    except ImportError:
        logger.debug("sentry-sdk not installed; error reporting disabled")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to initialize error reporting: %s", exc)


def _scrub_event(event: dict, hint: dict) -> dict:  # type: ignore[type-arg]
    """Remove sensitive data before sending."""
    if "extra" in event:
        for key in list(event["extra"]):
            if any(s in key.lower() for s in _SCRUB_KEY_KEYWORDS):
                event["extra"][key] = "[REDACTED]"

    if "breadcrumbs" in event and "values" in event["breadcrumbs"]:
        for breadcrumb in event["breadcrumbs"]["values"]:
            if "message" in breadcrumb:
                msg = str(breadcrumb["message"])
                sensitive = any(s in msg.lower() for s in _SCRUB_MSG_KEYWORDS)
                if sensitive or _SCRUB_KEY_PATTERN.search(msg):
                    breadcrumb["message"] = "[REDACTED]"
            if "data" in breadcrumb and isinstance(breadcrumb["data"], dict):
                for key in list(breadcrumb["data"]):
                    if any(s in key.lower() for s in _SCRUB_KEY_KEYWORDS):
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
        logger.debug("sentry-sdk not installed; MQTT dump not sent")
        return False

    if not sentry_sdk.is_initialized():
        logger.debug("Error reporting not initialized; MQTT dump not sent")
        return False

    trimmed = messages[-max_messages:] if len(messages) > max_messages else messages
    scrubbed = [_scrub_mqtt_envelope(m) for m in trimmed]
    dump = json.dumps(scrubbed, indent=2, ensure_ascii=False)
    if len(dump) > max_payload_chars:
        dump = dump[: max_payload_chars] + "\n... (truncated)"

    sentry_sdk.capture_message(
        "MQTT dump (user-reported)",
        level="info",
        extras={"mqtt_dump": dump, "message_count": len(scrubbed)},
    )
    logger.info("MQTT dump sent to GlitchTip (%d messages)", len(scrubbed))
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
