"""BrokerClient — verb-shaped helpers backed by BrokerAuthClient.

Mirrors the portal's portal/src/api/client.ts shape:
  - get / post / put / delete return UNWRAPPED data, raise BrokerError on failure.
  - 401 on a non-auth endpoint → call force_new_token + replay once.
  - Network failures → BrokerError.transport(...).

This is what tools consume. They don't see auth tokens, cookies,
envelopes, or HTTP status codes — just typed return values or an
exception.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from openvdi_admin.auth import BrokerAuthClient
from openvdi_admin.config import Settings
from openvdi_admin.errors import BrokerError, unwrap_envelope


logger = logging.getLogger(__name__)


class BrokerClient:
    """Verb-shaped HTTP helpers. Owns its own httpx.AsyncClient
    distinct from BrokerAuthClient's — so auth endpoint calls don't
    accidentally trip the 401-replay path here.
    """

    def __init__(self, auth: BrokerAuthClient, settings: Settings) -> None:
        self._auth = auth
        self._http = httpx.AsyncClient(
            base_url=str(settings.openvdi_broker_url).rstrip("/"),
            verify=settings.openvdi_verify_ssl,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
            ),
            timeout=httpx.Timeout(30.0),
        )

    async def close(self) -> None:
        await self._http.aclose()

    # ── Verb helpers ───────────────────────────────────────────

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return await self._request("GET", path, params=params, body=None)

    async def post(
        self,
        path: str,
        *,
        body: Any = None,
    ) -> Any:
        return await self._request("POST", path, params=None, body=body)

    async def put(
        self,
        path: str,
        *,
        body: Any,
    ) -> Any:
        return await self._request("PUT", path, params=None, body=body)

    async def delete(
        self,
        path: str,
    ) -> Any:
        return await self._request("DELETE", path, params=None, body=None)

    # ── Request loop ───────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        body: Any,
    ) -> Any:
        token = await self._auth.get_token()
        response = await self._attempt(method, path, params, body, token)

        # On 401: refresh + replay once. Per A5 / S4: ONE replay,
        # never a loop — broken-state cluster shouldn't generate a
        # storm.
        if response.status_code == 401:
            new_token = await self._auth.force_new_token()
            response = await self._attempt(
                method, path, params, body, new_token,
            )

        return self._handle(response)

    async def _attempt(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        body: Any,
        token: str,
    ) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        try:
            return await self._http.request(
                method,
                path,
                params=params,
                json=body if body is not None else None,
                headers=headers,
            )
        except httpx.RequestError as exc:
            raise BrokerError.transport(
                f"{method} {path}: {type(exc).__name__}: {exc}"
            ) from exc

    def _handle(self, response: httpx.Response) -> Any:
        # 204 No Content — destructive endpoints sometimes return
        # this directly (e.g. DELETE /sessions/{id} per M2-16).
        if response.status_code == 204:
            return None

        try:
            payload = response.json()
        except ValueError as exc:
            raise BrokerError.envelope_missing(
                response.status_code
            ) from exc

        return unwrap_envelope(payload, http_status=response.status_code)
