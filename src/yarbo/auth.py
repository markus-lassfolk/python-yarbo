"""
yarbo.auth — JWT authentication and RSA password encryption for the Yarbo cloud API.

The Yarbo backend uses Auth0 JWTs (RS256) issued by
``dev-6ubfuqym1d3m0mq1.us.auth0.com``. Passwords are RSA-PKCS#1 v1.5 encrypted
before transmission (despite OAEP references in the Dart code — confirmed by
live testing that PKCS1v15 is the actual padding used).

The RSA public key is bundled in the Yarbo app APK at:
    ``assets/rsa_key/rsa_public_key.pem``

References:
    yarbo-reversing/yarbo/auth.py — original synchronous implementation
    yarbo-reversing/docs/API_ENDPOINTS.md — endpoint documentation
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
import time

import aiohttp

from .exceptions import YarboAuthError, YarboConnectionError, YarboTokenExpiredError

logger = logging.getLogger(__name__)

# REST endpoint paths
_LOGIN_PATH = "/yarbo/robot-service/robot/commonUser/login"
_REFRESH_PATH = "/yarbo/robot-service/robot/commonUser/refreshToken"
_LOGOUT_PATH = "/yarbo/robot-service/robot/commonUser/logout"


class YarboAuth:
    """
    Manages authentication state for the Yarbo cloud API.

    Handles login (RSA-encrypted password), token refresh, and logout.
    Tokens are stored in memory; no persistence is provided by this class.

    Args:
        base_url:     REST API gateway base URL.
        username:     User email address.
        password:     Plaintext password (encrypted before transmission).
        rsa_key_path: Path to the RSA public key PEM extracted from the APK.
                      If not provided, falls back to the vendored key in the
                      package (if available).
        session:      Existing ``aiohttp.ClientSession`` to reuse.
    """

    def __init__(
        self,
        base_url: str,
        username: str = "",
        password: str = "",
        rsa_key_path: Path | str | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._key_path = Path(rsa_key_path) if rsa_key_path else self._default_key_path()
        self._session = session
        self._owns_session = session is None

        self._public_key: object = None  # lazy-loaded

        # Token state
        self.access_token: str = ""
        self.refresh_token: str = ""
        self.expires_at: float = 0.0
        self.user_id: str = ""
        self.sn_list: list[str] = []

    @staticmethod
    def _default_key_path() -> Path:
        """
        Return the vendored RSA key path (package-relative).

        .. warning::
            The vendored key at ``src/yarbo/keys/rsa_public_key.pem`` is a
            **placeholder** — cloud auth will fail until it is replaced with
            the real key extracted from the Yarbo APK.

            See ``src/yarbo/keys/README.md`` for extraction instructions, or
            supply the real key path via ``rsa_key_path`` at construction time.
        """
        return Path(__file__).parent / "keys" / "rsa_public_key.pem"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def login(self) -> None:
        """
        Perform a full login using username and RSA-encrypted password.

        Stores tokens in ``access_token`` / ``refresh_token`` / ``expires_at``.

        Raises:
            YarboAuthError:       If credentials are rejected.
            YarboConnectionError: On network failure.
        """
        enc_pw = self._encrypt_password(self._password)
        payload = {"username": self._username, "password": enc_pw}
        data = await self._post(_LOGIN_PATH, payload, require_auth=False)
        self._store_tokens(data.get("data", {}))
        logger.info("Login successful for %s (sn_list=%s)", self._username, self.sn_list)

    async def refresh(self) -> None:
        """
        Refresh the access token using the stored refresh token.

        Raises:
            YarboAuthError:          If no refresh token is available.
            YarboTokenExpiredError:  If the refresh token has expired.
        """
        if not self.refresh_token:
            raise YarboAuthError("No refresh token available — call login() first.")
        data = await self._post(
            _REFRESH_PATH,
            {"refreshToken": self.refresh_token},
            require_auth=False,
        )
        self._store_tokens(data.get("data", {}))
        logger.debug("Token refreshed, expires_at=%s", self.expires_at)

    async def logout(self) -> None:
        """Invalidate the current access token on the server."""
        if not self.access_token:
            return
        try:
            await self._post(_LOGOUT_PATH, {})
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.access_token = ""
            self.refresh_token = ""
            self.expires_at = 0.0

    async def ensure_valid_token(self) -> None:
        """
        Ensure the access token is valid, refreshing or re-logging in as needed.

        Refreshes 60 seconds before expiry. Falls back to full login if the
        refresh token has also expired.
        """
        if not self.access_token:
            await self.login()
            return
        if time.time() >= self.expires_at - 60:
            try:
                await self.refresh()
            except YarboAuthError:
                await self.login()

    @property
    def is_authenticated(self) -> bool:
        """True if a non-expired access token is currently held."""
        return bool(self.access_token) and time.time() < self.expires_at

    @property
    def auth_headers(self) -> dict[str, str]:
        """Authorization headers for authenticated REST requests."""
        return {"Authorization": f"Bearer {self.access_token}"}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_public_key(self) -> object:
        if self._public_key is None:
            try:
                from cryptography.hazmat.primitives import serialization  # noqa: PLC0415

                with Path(self._key_path).open("rb") as fh:
                    self._public_key = serialization.load_pem_public_key(fh.read())
            except FileNotFoundError as exc:
                raise YarboAuthError(
                    f"RSA public key not found at {self._key_path}. "
                    "Extract it from the Yarbo APK: assets/rsa_key/rsa_public_key.pem"
                ) from exc
            except ImportError as exc:
                raise YarboAuthError(
                    "The 'cryptography' package is required for RSA login: "
                    "pip install 'python-yarbo[cloud]'"
                ) from exc
        return self._public_key

    def _encrypt_password(self, plaintext: str) -> str:
        """
        Encrypt the password with RSA PKCS#1 v1.5 and return base64 ciphertext.

        NOTE: PKCS1v15 is the ACTUAL padding used despite OAEP references in
        the Flutter source. Confirmed by live testing with the production API.
        """
        from cryptography.hazmat.primitives.asymmetric import padding  # noqa: PLC0415

        pub_key = self._load_public_key()
        ciphertext = pub_key.encrypt(plaintext.encode("utf-8"), padding.PKCS1v15())  # type: ignore[union-attr]
        return base64.b64encode(ciphertext).decode("utf-8")

    def _store_tokens(self, data: dict) -> None:
        self.access_token = data.get("accessToken", "")
        self.refresh_token = data.get("refreshToken", "")
        self.user_id = data.get("userId", "")
        expires_in = data.get("expiresIn", 0)
        self.expires_at = time.time() + expires_in
        self.sn_list = data.get("snList", [])

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _post(
        self,
        path: str,
        body: dict,
        require_auth: bool = True,
    ) -> dict:
        session = await self._get_session()
        headers = {"Content-Type": "application/json"}
        if require_auth and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        url = self._base_url + path
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            async with session.post(url, json=body, headers=headers, timeout=timeout) as resp:
                if resp.status == 401:
                    raise YarboTokenExpiredError(f"401 Unauthorized on {path}")
                if resp.status == 403:
                    raise YarboAuthError(
                        f"403 Forbidden on {path} (may require AWS-SigV4 auth)"
                    )
                data = await resp.json(content_type=None)
        except aiohttp.ClientError as exc:
            raise YarboConnectionError(f"Network error on {path}: {exc}") from exc

        if not data.get("success", False):
            msg = data.get("message", "unknown error")
            raise YarboAuthError(f"Auth request failed ({path}): {msg}")
        return data
