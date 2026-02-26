"""
yarbo.exceptions — Custom exception hierarchy for the python-yarbo library.

All exceptions raised by the library are subclasses of ``YarboError``,
making it easy to catch them with a single ``except YarboError`` clause.

Hierarchy::

    YarboError
    ├── YarboConnectionError       # MQTT or HTTP connection failed
    │   └── YarboTimeoutError      # Connection or command timed out
    ├── YarboProtocolError         # Unexpected protocol response
    ├── YarboAuthError             # Authentication / authorisation failure
    │   └── YarboTokenExpiredError # JWT / refresh token expired
    └── YarboCommandError          # Robot rejected a command
        └── YarboNotControllerError # Not the active controller (need get_controller)
"""

from __future__ import annotations


class YarboError(Exception):
    """Base class for all python-yarbo exceptions."""


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


class YarboConnectionError(YarboError):
    """
    MQTT broker connection or HTTP request failed at the network level.

    Raised when the library cannot reach the broker or API gateway
    (DNS failure, connection refused, TLS handshake error, etc.).
    """


class YarboTimeoutError(YarboConnectionError):
    """
    A connection attempt or command response timed out.

    Raised when the broker does not respond within the configured timeout.
    """


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class YarboProtocolError(YarboError):
    """
    Unexpected or malformed data received from the broker or API.

    Raised when payload decompression, JSON parsing, or schema validation fails.
    """


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class YarboAuthError(YarboError):
    """
    Authentication or authorisation failed.

    Raised on HTTP 401/403 responses or when JWT validation fails.
    """


class YarboTokenExpiredError(YarboAuthError):
    """
    The JWT access token or refresh token has expired.

    Call ``YarboCloudClient.login()`` again to obtain a new token.
    """


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


class YarboCommandError(YarboError):
    """
    The robot rejected a command or returned an error response.

    Attributes:
        code:    Yarbo error code string (e.g. ``"B0001"``).
        message: Human-readable error message from the device.
    """

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code

    def __str__(self) -> str:
        if self.code:
            return f"YarboCommandError(code={self.code!r}): {self.args[0]}"
        return f"YarboCommandError: {self.args[0]}"


class YarboNotControllerError(YarboCommandError):
    """
    The app is not the active controller of the robot.

    The Yarbo MQTT protocol requires a ``get_controller`` handshake before
    most action commands. Call ``await client.get_controller()`` first.
    """
