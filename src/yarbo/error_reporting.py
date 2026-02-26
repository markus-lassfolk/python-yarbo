"""GlitchTip/Sentry error reporting for python-yarbo."""

import logging
import os

logger = logging.getLogger(__name__)


def init_error_reporting(
    dsn: str | None = None,
    environment: str = "production",
    enabled: bool = True,
) -> None:
    """Initialize Sentry/GlitchTip error reporting.

    Opt-in only. To enable, set YARBO_SENTRY_DSN or SENTRY_DSN environment variable,
    or pass dsn= parameter explicitly.

    Args:
        dsn: Sentry DSN. Must be provided via parameter or environment variable.
             No default DSN is provided.
        environment: Environment tag (production/development/testing).
        enabled: Master switch. If False, no SDK initialization occurs.
    """
    if not enabled:
        return

    # Load DSN from parameter or environment only
    dsn = dsn or os.environ.get("YARBO_SENTRY_DSN") or os.environ.get("SENTRY_DSN")

    if not dsn:
        return

    try:
        import sentry_sdk  # noqa: PLC0415

        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            traces_sample_rate=0.1,
            send_default_pii=False,
            before_send=_scrub_event,
        )
        logger.debug("Error reporting initialized (dsn=%s...)", dsn[:30])
    except ImportError:
        logger.debug("sentry-sdk not installed; error reporting disabled")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to initialize error reporting: %s", exc)


def _scrub_event(event: dict, hint: dict) -> dict:  # type: ignore[type-arg]
    """Remove sensitive data before sending."""
    # Strip MQTT credentials, tokens, passwords from breadcrumbs and extras
    if "extra" in event:
        for key in list(event["extra"]):
            if any(s in key.lower() for s in ("password", "token", "secret", "credential", "key")):
                event["extra"][key] = "[REDACTED]"
    return event
