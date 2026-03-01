"""GlitchTip/Sentry error reporting for the python-yarbo library."""

from __future__ import annotations

import logging
import os
import re

_LOGGER = logging.getLogger(__name__)

# Default DSN for the python-yarbo GlitchTip project.
# Opt-out: set YARBO_SENTRY_DSN="" to disable error reporting.
_DEFAULT_DSN = "https://c690590f8f664d609f6abe4cb0392d53@villapolly.duckdns.org/2"


def init_error_reporting(
    dsn: str | None = None,
    environment: str = "production",
    enabled: bool = True,
    tags: dict[str, str] | None = None,
) -> None:
    """Initialize Sentry/GlitchTip error reporting for python-yarbo.

    Enabled by default during the beta — errors are reported to help identify
    and fix bugs. No PII is collected; credentials are scrubbed before sending.

    To disable, set the YARBO_SENTRY_DSN environment variable to an empty string.
    To use a custom DSN, set YARBO_SENTRY_DSN to your project DSN.

    Args:
        dsn: Sentry DSN override. If None, falls back to YARBO_SENTRY_DSN env var,
             then to the built-in default DSN.
        environment: Environment tag (production/development/testing).
        enabled: Master switch. If False, no SDK initialization occurs.
        tags: Optional extra tags (e.g. robot_serial, library_version).
    """
    if not enabled:
        return

    # Resolve DSN: explicit arg > YARBO_SENTRY_DSN env var > built-in default
    env_dsn = os.environ.get("YARBO_SENTRY_DSN")
    if env_dsn is not None and env_dsn == "":
        _LOGGER.debug("Error reporting explicitly disabled via YARBO_SENTRY_DSN=\"\"")
        return
    effective_dsn = dsn or env_dsn or _DEFAULT_DSN

    if not effective_dsn:
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
