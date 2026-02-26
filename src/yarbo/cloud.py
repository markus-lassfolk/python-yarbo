"""
yarbo.cloud — YarboCloudClient: REST API + JWT auth for the Yarbo cloud.

Provides access to account management, robot binding/unbinding, notification
settings, and other cloud-only features.

NOTE on API migration (as of 2026-02-25):
    The Yarbo backend is actively migrating from plain JWT Bearer tokens to
    AWS SigV4 (IAM/Cognito) auth. Many endpoints that previously accepted JWT
    now return 403. Endpoints marked ✅ work with Bearer auth.
    See yarbo-reversing/yarbo/client.py for a full status inventory.

References:
    yarbo-reversing/yarbo/client.py — synchronous reference implementation
    yarbo-reversing/docs/API_ENDPOINTS.md — endpoint documentation
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import aiohttp

from .auth import YarboAuth
from .const import REST_BASE_URL
from .exceptions import YarboAuthError, YarboConnectionError
from .models import YarboRobot

if TYPE_CHECKING:
    from types import TracebackType

logger = logging.getLogger(__name__)


class YarboCloudClient:
    """
    Async REST API client for the Yarbo cloud backend.

    Handles JWT authentication with automatic token refresh. Use as an
    async context manager or call :meth:`connect` / :meth:`disconnect` manually.

    Example::

        async with YarboCloudClient(username="user@example.com", password="secret") as client:
            robots = await client.list_robots()
            for robot in robots:
                print(robot.sn, robot.name, robot.is_online)

    Args:
        username:     User email address.
        password:     Plaintext password.
        base_url:     Override the REST gateway base URL.
        rsa_key_path: Path to the RSA public key PEM (extracted from APK).
    """

    def __init__(
        self,
        username: str = "",
        password: str = "",
        base_url: str = REST_BASE_URL,
        rsa_key_path: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None
        self.auth = YarboAuth(
            base_url=self._base_url,
            username=username,
            password=password,
            rsa_key_path=rsa_key_path,
        )

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> YarboCloudClient:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.disconnect()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Create the HTTP session and log in."""
        self._session = aiohttp.ClientSession(
            headers={"Content-Type": "application/json"},
        )
        self.auth._session = self._session  # share session
        await self.auth.login()

    async def disconnect(self) -> None:
        """Log out and close the HTTP session."""
        await self.auth.logout()
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Internal request helper
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        require_auth: bool = True,
    ) -> dict[str, Any]:
        """
        Send an authenticated REST request and return the parsed ``data`` dict.

        Handles token refresh, 401/403 translation, and the Yarbo API's
        ``{"success": bool, "data": {...}, "message": str}`` envelope.

        Args:
            method:       HTTP method (``"GET"`` or ``"POST"``).
            path:         API path relative to the base URL.
            body:         JSON body for POST requests.
            params:       Query parameters for GET requests.
            require_auth: If True, ensure a valid token and add Authorization header.

        Returns:
            The ``data`` dict from the API response envelope.

        Raises:
            YarboConnectionError: On network failure or if not connected.
            YarboAuthError:       On 401/403 responses.
            YarboCommandError:    If ``success`` is ``False`` in the response.
        """
        if require_auth:
            await self.auth.ensure_valid_token()

        if self._session is None or self._session.closed:
            raise YarboConnectionError("Client is not connected. Call connect() first.")

        headers: dict[str, str] = {}
        if require_auth:
            headers.update(self.auth.auth_headers)

        url = self._base_url + path
        request_method = getattr(self._session, method.lower(), None)
        if request_method is None:
            raise ValueError(f"Unsupported HTTP method: {method!r}")

        kwargs: dict[str, Any] = {"headers": headers}
        if method == "GET":
            kwargs["params"] = params
        else:
            kwargs["json"] = body or {}

        try:
            async with request_method(url, **kwargs) as resp:
                if resp.status == 401:
                    raise YarboAuthError(f"401 Unauthorized on {path}")
                if resp.status == 403:
                    raise YarboAuthError(
                        f"403 Forbidden on {path}. "
                        "This endpoint may require AWS-SigV4 auth (not plain JWT)."
                    )
                data: dict[str, Any] = await resp.json(content_type=None)
        except aiohttp.ClientError as exc:
            raise YarboConnectionError(f"Network error on {path}: {exc}") from exc

        if not data.get("success", False):
            from .exceptions import YarboCommandError  # noqa: PLC0415
            raise YarboCommandError(
                data.get("message", "unknown error"),
                code=data.get("code", ""),
            )
        return data.get("data", {})

    # ------------------------------------------------------------------
    # Robot management
    # ------------------------------------------------------------------

    async def list_robots(self) -> list[YarboRobot]:
        """
        List all robots bound to this account.  ✅

        Returns:
            List of :class:`~yarbo.models.YarboRobot` instances.

        REST: ``GET /yarbo/robot-service/commonUser/userRobotBind/getUserRobotBindVos``
        """
        data = await self._request(
            "GET",
            "/yarbo/robot-service/commonUser/userRobotBind/getUserRobotBindVos",
        )
        return [YarboRobot.from_dict(r) for r in data.get("deviceList", [])]

    async def bind_robot(self, sn: str) -> dict[str, Any]:
        """
        Bind a robot to this account.  ✅ (needs valid SN)

        Args:
            sn: Robot serial number.

        REST: ``POST /yarbo/robot-service/robot/commonUser/bindUserRobot``
        """
        return await self._request(
            "POST",
            "/yarbo/robot-service/robot/commonUser/bindUserRobot",
            {"sn": sn},
        )

    async def unbind_robots(self, serial_nums: list[str]) -> dict[str, Any]:
        """
        Unbind robots from this account.  ✅ (needs valid SNs)

        Args:
            serial_nums: List of serial numbers to unbind.

        REST: ``POST /yarbo/robot-service/commonUser/userRobotBind/unbind``
        """
        return await self._request(
            "POST",
            "/yarbo/robot-service/commonUser/userRobotBind/unbind",
            {"serialNums": serial_nums},
        )

    async def rename_robot(self, sn: str, name: str) -> dict[str, Any]:
        """
        Rename a robot.  ✅ (needs valid SN)

        Args:
            sn:   Robot serial number.
            name: New display name.

        REST: ``POST /yarbo/robot-service/robot/commonUser/updateSnowbotName``
        """
        return await self._request(
            "POST",
            "/yarbo/robot-service/robot/commonUser/updateSnowbotName",
            {"sn": sn, "name": name},
        )

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    async def get_notification_settings(self) -> dict[str, Any]:
        """
        Get push notification preferences.  ✅

        Returns:
            Dict with keys: ``mobileSystemNotification``, ``generalNotification``,
            ``errNotification`` (1=on, 0=off).

        REST: ``GET /yarbo/msg/getNotificationSetting``
        """
        return await self._request("GET", "/yarbo/msg/getNotificationSetting")

    async def get_device_messages(self) -> list[dict[str, Any]]:
        """
        Get device-level alert messages.  ✅

        REST: ``GET /yarbo/msg/userDeviceMsg``
        """
        data = await self._request("GET", "/yarbo/msg/userDeviceMsg")
        return data.get("deviceMsg", [])

    # ------------------------------------------------------------------
    # App version
    # ------------------------------------------------------------------

    async def get_latest_version(self) -> dict[str, Any]:
        """
        Get the latest app, firmware, and dock-controller versions.  ✅

        Live data (2026-02-24):
            - ``appVersion``: ``"3.16.3"``
            - ``firmwareVersion``: ``"3.11.0"``
            - ``dcVersion``: ``"1.0.25"``

        REST: ``GET /yarbo/commonUser/getLatestPubVersion``
        """
        return await self._request("GET", "/yarbo/commonUser/getLatestPubVersion")
