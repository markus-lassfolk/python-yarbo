"""Tests for yarbo.auth — JWT auth and RSA password encryption."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from yarbo.auth import YarboAuth
from yarbo.exceptions import YarboAuthError

MOCK_LOGIN_RESPONSE = {
    "success": True,
    "code": "00000",
    "message": "ok",
    "data": {
        "accessToken": "eyJfake.token.here",
        "refreshToken": "v1.fake-refresh-token",
        "userId": "user@example.com",
        "expiresIn": 2592000,
        "snList": ["24400102L8HO5227"],
    },
}

MOCK_REFRESH_RESPONSE = {
    "success": True,
    "code": "00000",
    "data": {
        "accessToken": "eyJrefreshed.token",
        "refreshToken": "v1.new-refresh-token",
        "userId": "user@example.com",
        "expiresIn": 2592000,
        "snList": [],
    },
}


def make_auth(username="user@example.com", password="secret") -> YarboAuth:
    return YarboAuth(
        base_url="https://fake.api.example.com",
        username=username,
        password=password,
    )


@pytest.mark.asyncio
class TestYarboAuthLogin:
    async def test_login_stores_tokens(self):
        auth = make_auth()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=MOCK_LOGIN_RESPONSE)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch.object(auth, "_encrypt_password", return_value="encrypted_pw"), \
             patch.object(auth, "_get_session") as mock_session_fn:
            session = MagicMock()
            session.post = MagicMock(return_value=mock_resp)
            mock_session_fn.return_value = session

            await auth.login()

        assert auth.access_token == "eyJfake.token.here"
        assert auth.refresh_token == "v1.fake-refresh-token"
        assert auth.user_id == "user@example.com"
        assert auth.sn_list == ["24400102L8HO5227"]
        assert auth.expires_at > time.time()

    async def test_login_raises_on_failure(self):
        auth = make_auth()
        failure_response = {"success": False, "message": "Invalid credentials"}
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=failure_response)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch.object(auth, "_encrypt_password", return_value="enc"), \
             patch.object(auth, "_get_session") as mock_session_fn:
            session = MagicMock()
            session.post = MagicMock(return_value=mock_resp)
            mock_session_fn.return_value = session

            with pytest.raises(YarboAuthError, match="Invalid credentials"):
                await auth.login()


@pytest.mark.asyncio
class TestYarboAuthState:
    async def test_is_authenticated_true(self):
        auth = make_auth()
        auth.access_token = "token"
        auth.expires_at = time.time() + 3600
        assert auth.is_authenticated is True

    async def test_is_authenticated_false_when_expired(self):
        auth = make_auth()
        auth.access_token = "token"
        auth.expires_at = time.time() - 1
        assert auth.is_authenticated is False

    async def test_is_authenticated_false_when_no_token(self):
        auth = make_auth()
        assert auth.is_authenticated is False

    async def test_auth_headers(self):
        auth = make_auth()
        auth.access_token = "mytoken"
        headers = auth.auth_headers
        assert headers["Authorization"] == "Bearer mytoken"


@pytest.mark.asyncio
class TestYarboAuthEnsureValid:
    async def test_ensure_valid_calls_login_when_no_token(self):
        auth = make_auth()
        with patch.object(auth, "login", new_callable=AsyncMock) as mock_login:
            await auth.ensure_valid_token()
            mock_login.assert_called_once()

    async def test_ensure_valid_calls_refresh_when_expiring(self):
        auth = make_auth()
        auth.access_token = "old_token"
        auth.refresh_token = "refresh"
        auth.expires_at = time.time() + 30  # expiring soon (< 60s)

        with patch.object(auth, "refresh", new_callable=AsyncMock) as mock_refresh:
            await auth.ensure_valid_token()
            mock_refresh.assert_called_once()

    async def test_ensure_valid_does_nothing_when_valid(self):
        auth = make_auth()
        auth.access_token = "valid_token"
        auth.expires_at = time.time() + 3600

        with patch.object(auth, "login", new_callable=AsyncMock) as mock_login, \
             patch.object(auth, "refresh", new_callable=AsyncMock) as mock_refresh:
            await auth.ensure_valid_token()
            mock_login.assert_not_called()
            mock_refresh.assert_not_called()

    async def test_ensure_valid_falls_back_to_login_if_refresh_fails(self):
        auth = make_auth()
        auth.access_token = "old"
        auth.refresh_token = "expired_refresh"
        auth.expires_at = time.time() - 1

        with patch.object(auth, "refresh", side_effect=YarboAuthError("expired")), \
             patch.object(auth, "login", new_callable=AsyncMock) as mock_login:
            await auth.ensure_valid_token()
            mock_login.assert_called_once()
