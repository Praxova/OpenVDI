"""Low-level Proxmox HTTP client.

Internal to the Proxmox provider package. Do not import from outside
`app.providers.proxmox`. The provider class wraps this client and exposes
only the `HypervisorProvider` surface to the broker.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.providers.exceptions import (
    ProviderAuthError,
    ProviderLockError,
    ProviderNotFoundError,
    ProviderTimeoutError,
)

from .exceptions import ProxmoxError
from .params import translate_params

logger = logging.getLogger(__name__)


def _looks_like_lock_error(body: str) -> bool:
    """Crude match on Proxmox's lock-contention messages.

    Covers 'can't lock file', 'trying to acquire lock', 'VM is locked',
    'locked (lock=backup)', etc. Both 'lock' and 'locked' match on 'lock'.
    """
    return "lock" in body.lower()


class _ProxmoxClient:
    """httpx wrapper for the Proxmox VE REST API.

    One instance per cluster. Owns an httpx.AsyncClient with connection
    pooling and token-based auth. Methods here translate snake_case to
    kebab-case, map HTTP status codes to ProviderError subclasses, and
    retry transient failures with exponential backoff.
    """

    def __init__(
        self,
        api_url: str,
        token_id: str,
        token_secret: str,
        verify_ssl: bool = True,
        max_connections: int = 20,
        max_keepalive: int = 10,
        default_timeout: float = 30.0,
    ) -> None:
        api_url = api_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=f"{api_url}/api2/json",
            verify=verify_ssl,
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_keepalive,
            ),
            timeout=httpx.Timeout(default_timeout),
            headers={
                "Authorization": f"PVEAPIToken={token_id}={token_secret}",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── The one request dispatcher ────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        data: dict | None = None,
        timeout: float | None = None,
        max_retries: int = 3,
        retry_backoff: float = 2.0,
    ) -> Any:
        """Issue one API call and return the 'data' field of the response.

        Translates snake_case parameter names. Retries on 5xx / network /
        timeout with exponential backoff. Maps HTTP status codes to
        ProviderError subclasses.
        """
        if not path.startswith("/"):
            path = "/" + path

        translated_params = translate_params(params)
        translated_data = translate_params(data)

        request_kwargs: dict[str, Any] = {}
        if translated_params:
            request_kwargs["params"] = translated_params
        if translated_data:
            request_kwargs["data"] = translated_data
        if timeout is not None:
            request_kwargs["timeout"] = timeout

        last_lock_body: str | None = None
        last_error: Exception | None = None

        for attempt in range(max_retries):
            try:
                logger.debug(
                    "proxmox request",
                    extra={"method": method, "path": path, "attempt": attempt},
                )
                resp = await self._client.request(
                    method, path, **request_kwargs
                )
            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt < max_retries - 1:
                    sleep_s = retry_backoff * (2 ** attempt)
                    logger.warning(
                        "proxmox timeout; retrying",
                        extra={
                            "method": method, "path": path,
                            "attempt": attempt + 1,
                            "max_retries": max_retries,
                            "sleep_s": sleep_s,
                        },
                    )
                    await asyncio.sleep(sleep_s)
                    continue
                raise ProviderTimeoutError(
                    f"request timed out after {max_retries} attempts",
                    provider_type="proxmox",
                    detail={"endpoint": path, "method": method, "error": str(exc)},
                ) from exc
            except httpx.HTTPError as exc:
                # NetworkError, ConnectError, etc.
                last_error = exc
                if attempt < max_retries - 1:
                    sleep_s = retry_backoff * (2 ** attempt)
                    logger.warning(
                        "proxmox network error; retrying",
                        extra={
                            "method": method, "path": path,
                            "attempt": attempt + 1,
                            "max_retries": max_retries,
                            "sleep_s": sleep_s,
                            "error": str(exc),
                        },
                    )
                    await asyncio.sleep(sleep_s)
                    continue
                raise ProxmoxError(
                    status_code=0,
                    message=f"network error after {max_retries} attempts: {exc}",
                    endpoint=path,
                ) from exc

            # Got a response. Dispatch on status code.
            status = resp.status_code

            if 200 <= status < 300:
                return self._parse_body(resp, path)

            body = _response_body(resp)

            if status in (401, 403):
                raise ProviderAuthError(
                    body or "authentication failed",
                    provider_type="proxmox",
                    detail={"status_code": status, "endpoint": path},
                )

            if status == 404:
                raise ProviderNotFoundError(
                    body or "resource not found",
                    provider_type="proxmox",
                    detail={"status_code": status, "endpoint": path},
                )

            if status >= 500:
                if _looks_like_lock_error(body):
                    last_lock_body = body
                    if attempt < max_retries - 1:
                        sleep_s = retry_backoff * (2 ** attempt)
                        logger.warning(
                            "proxmox lock error; retrying",
                            extra={
                                "method": method, "path": path,
                                "attempt": attempt + 1,
                                "max_retries": max_retries,
                                "sleep_s": sleep_s,
                            },
                        )
                        await asyncio.sleep(sleep_s)
                        continue
                    raise ProviderLockError(
                        body,
                        provider_type="proxmox",
                        detail={"status_code": status, "endpoint": path},
                    )

                # Non-lock 5xx: retry with same backoff.
                if attempt < max_retries - 1:
                    sleep_s = retry_backoff * (2 ** attempt)
                    logger.warning(
                        "proxmox server error; retrying",
                        extra={
                            "method": method, "path": path,
                            "status": status,
                            "attempt": attempt + 1,
                            "max_retries": max_retries,
                            "sleep_s": sleep_s,
                        },
                    )
                    await asyncio.sleep(sleep_s)
                    continue
                raise ProxmoxError(
                    status_code=status, message=body, endpoint=path
                )

            # 4xx non-auth, non-404: client bug, do not retry.
            raise ProxmoxError(
                status_code=status, message=body, endpoint=path
            )

        # Shouldn't reach here — the loop body either returns or raises.
        raise ProxmoxError(
            status_code=0,
            message=(
                f"exhausted {max_retries} attempts without a terminal response; "
                f"last error: {last_error} / last lock body: {last_lock_body}"
            ),
            endpoint=path,
        )

    # ── Body parsing ──────────────────────────────────────────

    @staticmethod
    def _parse_body(resp: httpx.Response, path: str) -> Any:
        """Parse a 2xx response's JSON envelope and return `data`.

        Proxmox wraps responses in {"data": ...}. `data` may be a dict,
        list, str (UPID), or null. Returns whatever is under `data`.
        """
        if not resp.content:
            return None
        try:
            envelope = resp.json()
        except ValueError as exc:
            raise ProxmoxError(
                status_code=resp.status_code,
                message=f"non-JSON 2xx body: {resp.text[:200]!r}",
                endpoint=path,
            ) from exc
        if isinstance(envelope, dict):
            return envelope.get("data")
        return envelope

    # ── Typed wrappers used by the provider ───────────────────

    async def get_task_status_raw(self, node: str, upid: str) -> dict:
        """GET /nodes/{node}/tasks/{upid}/status. Returns the raw data dict."""
        result = await self._request("GET", f"/nodes/{node}/tasks/{upid}/status")
        if not isinstance(result, dict):
            raise ProxmoxError(
                status_code=0,
                message=f"task status returned non-dict: {type(result).__name__}",
                endpoint=f"/nodes/{node}/tasks/{upid}/status",
            )
        return result


def _response_body(resp: httpx.Response) -> str:
    """Best-effort body extraction for error messages.

    Tries JSON errors/message first, falls back to raw text.
    """
    try:
        envelope = resp.json()
    except ValueError:
        return resp.text.strip()
    if isinstance(envelope, dict):
        # Proxmox errors: {"data": null, "errors": {...}} or {"message": "..."}
        errors = envelope.get("errors")
        if errors:
            return str(errors)
        message = envelope.get("message")
        if message:
            return str(message)
    return resp.text.strip()
