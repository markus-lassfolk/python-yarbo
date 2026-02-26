"""Tests for yarbo.cloud — YarboCloudClient REST API."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from yarbo.cloud import YarboCloudClient
from yarbo.exceptions import YarboAuthError, YarboCommandError
from yarbo.models import YarboRobot


@pytest.fixture
def mock_auth():
    """Patch YarboAuth so cloud tests don't require real credentials."""
    with patch("yarbo.cloud.YarboAuth") as MockAuth:
        instance = MagicMock()
        instance.login = AsyncMock()
        instance.logout = AsyncMock()
        instance.ensure_valid_token = AsyncMock()
        instance.access_token = "fake_token"
        instance.auth_headers = {"Authorization": "Bearer fake_token"}
        instance._session = None
        MockAuth.return_value = instance
        yield instance


def _mock_response(data: dict, status: int = 200) -> MagicMock:
    """Create a mock aiohttp response."""
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value={"success": True, "data": data})
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


@pytest.mark.asyncio
class TestYarboCloudClientListRobots:
    async def test_list_robots_empty(self, mock_auth):
        client = YarboCloudClient(username="u", password="p")
        client._session = MagicMock(closed=False)

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"deviceList": []}
            robots = await client.list_robots()
            assert robots == []

    async def test_list_robots_returns_robot_objects(self, mock_auth):
        client = YarboCloudClient(username="u", password="p")
        client._session = MagicMock(closed=False)

        robot_data = {
            "deviceList": [
                {"sn": "YBG123", "name": "My Mower", "isOnline": True},
            ]
        }
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = robot_data
            robots = await client.list_robots()
            assert len(robots) == 1
            assert isinstance(robots[0], YarboRobot)
            assert robots[0].sn == "YBG123"
            assert robots[0].is_online is True


@pytest.mark.asyncio
class TestYarboCloudClientVersion:
    async def test_get_latest_version(self, mock_auth):
        client = YarboCloudClient(username="u", password="p")
        client._session = MagicMock(closed=False)

        version_data = {
            "appVersion": "3.16.3",
            "firmwareVersion": "3.11.0",
            "dcVersion": "1.0.25",
        }
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = version_data
            result = await client.get_latest_version()
            assert result["appVersion"] == "3.16.3"


@pytest.mark.asyncio
class TestYarboCloudClientNotifications:
    async def test_get_notification_settings(self, mock_auth):
        client = YarboCloudClient(username="u", password="p")
        client._session = MagicMock(closed=False)

        settings = {"mobileSystemNotification": 1, "generalNotification": 1, "errNotification": 1}
        with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = settings
            result = await client.get_notification_settings()
            assert result["mobileSystemNotification"] == 1


@pytest.mark.asyncio
class TestYarboCloudClientErrors:
    async def test_403_raises_auth_error(self, mock_auth):
        client = YarboCloudClient(username="u", password="p")
        import aiohttp

        mock_resp = MagicMock()
        mock_resp.status = 403
        mock_resp.json = AsyncMock(return_value={})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock(closed=False)
        session.get = MagicMock(return_value=mock_resp)
        client._session = session

        with pytest.raises(YarboAuthError, match="403 Forbidden"):
            await client._request("GET", "/some/endpoint")
