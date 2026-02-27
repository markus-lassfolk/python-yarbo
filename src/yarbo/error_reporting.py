"""GlitchTip/Sentry error reporting for python-yarbo."""

import logging
import re

logger = logging.getLogger(__name__)

_DEFAULT_DSN = "http://c690590f8f664d609f6abe4cb0392d53@192.168.1.99:8000/2"


def init_error_reporting(
    dsn: str | None = None,
    environment: str = "production",
    enabled: bool = True,
) -> None:
    """Initialize Sentry/GlitchTip error reporting.

    Enabled by default (opt-out). To disable, set YARBO_SENTRY_DSN="" or pass enabled=False.

    Args:
        dsn: Sentry DSN. Defaults to the python-yarbo GlitchTip project.
             Set YARBO_SENTRY_DSN="" to explicitly disable.
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

    dsn = dsn or env_dsn or os.environ.get("SENTRY_DSN") or _DEFAULT_DSN

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
    key_keywords = ("password", "token", "secret", "credential", "key")
    message_keywords = ("password", "token", "secret", "credential")
    key_pattern = re.compile(r"(?:_|api|access|auth|private)key", re.IGNORECASE)

    if "extra" in event:
        for key in list(event["extra"]):
            if any(s in key.lower() for s in key_keywords):
                event["extra"][key] = "[REDACTED]"

    if "breadcrumbs" in event and "values" in event["breadcrumbs"]:
        for breadcrumb in event["breadcrumbs"]["values"]:
            if "message" in breadcrumb:
                msg = str(breadcrumb["message"])
                if any(s in msg.lower() for s in message_keywords) or key_pattern.search(msg):
                    breadcrumb["message"] = "[REDACTED]"
            if "data" in breadcrumb and isinstance(breadcrumb["data"], dict):
                for key in list(breadcrumb["data"]):
                    if any(s in key.lower() for s in key_keywords):
                        breadcrumb["data"][key] = "[REDACTED]"

    return event
