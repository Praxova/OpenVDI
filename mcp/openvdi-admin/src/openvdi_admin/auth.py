"""BrokerAuthClient — manages the access token + refresh-cookie state.

Lifecycle:
  - Constructed once at MCP server startup with credentials + base URL.
  - Lazy login on first get_token() call.
  - On 401 from the broker: HTTP client calls force_new_token() to
    trigger a refresh (or login fallback) and gets a new token.
  - close() at MCP server shutdown to release the underlying
    httpx.AsyncClient.

The class owns its own httpx.AsyncClient (separate from the one
BrokerClient uses) so auth-endpoint calls don't go through the
401-replay path. The cookie jar lives on this AsyncClient — every
refresh call automatically sends the cookie set by the previous
login or refresh, no manual cookie wrangling needed.

Per A2 (lazy login), A3 (refresh-cookie flow), A5 (concurrent dedup),
A6 (no logout on shutdown).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Final

import httpx

from openvdi_admin.config import Settings
from openvdi_admin.errors import BrokerError


logger = logging.getLogger(__name__)


_LOGIN_PATH: Final = "/api/v1/auth/login"
_REFRESH_PATH: Final = "/api/v1/auth/refresh"


class BrokerAuthClient:
    """Owns the access token + the cookie jar that holds the refresh
    token. Hands tokens to BrokerClient on demand; refreshes on 401
    via the dedup pattern in `force_new_token()`."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._access_token: str | None = None
        self._refresh_in_flight: asyncio.Task[str] | None = None
        self._lock = asyncio.Lock()

        self._http = httpx.AsyncClient(
            base_url=str(settings.openvdi_broker_url).rstrip("/"),
            verify=settings.openvdi_verify_ssl,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
            ),
            timeout=httpx.Timeout(30.0),
            # The cookie jar lives on this AsyncClient. Every refresh
            # call automatically sends the cookie set by the previous
            # login or refresh.
            follow_redirects=False,
        )

    async def close(self) -> None:
        """Release the AsyncClient. Per A6, no logout call — refresh
        tokens auto-expire on the broker side."""
        await self._http.aclose()

    async def get_token(self) -> str:
        """Return a current access token, logging in if needed.

        Concurrent first-callers all wait on the same login (the
        asyncio.Lock + double-check pattern serializes them; the
        second caller through the lock sees a populated token and
        skips _do_login)."""
        if self._access_token is not None:
            return self._access_token
        async with self._lock:
            if self._access_token is None:
                await self._do_login()
            assert self._access_token is not None
            return self._access_token

    async def force_new_token(self) -> str:
        """Called by BrokerClient on 401. Returns a fresh access token.

        Concurrent callers (many in-flight requests all hitting 401
        because the same expired token was used) coalesce on a
        single in-flight refresh promise. Once the promise resolves,
        the next 401 starts a fresh promise."""
        async with self._lock:
            if (
                self._refresh_in_flight is not None
                and not self._refresh_in_flight.done()
            ):
                promise = self._refresh_in_flight
            else:
                promise = asyncio.create_task(self._do_refresh_or_login())
                self._refresh_in_flight = promise
        # Lock released; many callers can now await the same promise.
        return await promise

    # ── Internal: login / refresh ──────────────────────────────

    async def _do_login(self) -> str:
        """POST /auth/login. Stores access token. Caller MUST hold _lock."""
        try:
            response = await self._http.post(
                _LOGIN_PATH,
                json={
                    "username": self._settings.openvdi_service_user,
                    "password": (
                        self._settings.openvdi_service_password.get_secret_value()
                    ),
                },
            )
        except httpx.RequestError as exc:
            raise BrokerError.transport(
                f"login request failed: {exc}"
            ) from exc

        if response.status_code != 200:
            raise BrokerError(
                http_status=response.status_code,
                code="LOGIN_FAILED",
                message=f"login returned HTTP {response.status_code}",
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise BrokerError(
                http_status=response.status_code,
                code="LOGIN_FAILED",
                message="login response was not JSON",
            ) from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        token = data.get("access_token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise BrokerError(
                http_status=response.status_code,
                code="LOGIN_FAILED",
                message="login response missing access_token",
            )
        self._access_token = token
        logger.info(
            "MCP authenticated as %s", self._settings.openvdi_service_user,
        )
        return token

    async def _do_refresh(self) -> str:
        """POST /auth/refresh. The cookie jar carries the refresh token
        automatically. Stores new access token."""
        try:
            response = await self._http.post(_REFRESH_PATH)
        except httpx.RequestError as exc:
            raise BrokerError.transport(
                f"refresh request failed: {exc}"
            ) from exc

        if response.status_code == 401:
            # Refresh expired or revoked. Caller falls back to login.
            raise BrokerError(
                http_status=401,
                code="REFRESH_FAILED",
                message="refresh token rejected; will re-login",
            )
        if response.status_code != 200:
            raise BrokerError(
                http_status=response.status_code,
                code="REFRESH_FAILED",
                message=f"refresh returned HTTP {response.status_code}",
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise BrokerError(
                http_status=response.status_code,
                code="REFRESH_FAILED",
                message="refresh response was not JSON",
            ) from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        token = data.get("access_token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise BrokerError(
                http_status=response.status_code,
                code="REFRESH_FAILED",
                message="refresh response missing access_token",
            )
        self._access_token = token
        logger.debug("MCP token refreshed")
        return token

    async def _do_refresh_or_login(self) -> str:
        """Try refresh; on REFRESH_FAILED with HTTP 401, fall back to
        login.

        This is the body of the in-flight promise. NOT acquiring _lock
        here because force_new_token() already serialized access through
        the lock when SETTING the promise. Multiple awaiters of the
        same promise are reading the result, not racing.

        The lock is held only briefly to set up the promise; when this
        coroutine RUNS, the lock has already been released. That's
        intentional — long-running refresh shouldn't block other
        token reads. Mutations of self._access_token are inside
        _do_login / _do_refresh, which are reached from this coroutine
        only — no concurrent mutation.
        """
        try:
            return await self._do_refresh()
        except BrokerError as exc:
            if exc.code == "REFRESH_FAILED" and exc.http_status == 401:
                logger.info("MCP refresh expired; re-logging in")
                # Clear stale access token first so any reader that
                # gets the lock during login sees "no token" and waits.
                self._access_token = None
                return await self._do_login()
            raise
