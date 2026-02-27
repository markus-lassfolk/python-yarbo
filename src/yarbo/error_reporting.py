"""GlitchTip/Sentry error reporting for python-yarbo."""

import logging
import re

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

    Enabled by default (opt-out). To disable, set YARBO_SENTRY_DSN="" or pass enabled=False.

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
