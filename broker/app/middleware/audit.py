"""HTTP-level audit middleware — pure ASGI.

Writes an `audit_log` row for every admin mutation: POST/PUT/DELETE/
PATCH against `/api/v1/*`, excluding `/api/v1/me/*`. GETs are NOT
audited (W-4 — admin mutations only). User-router traffic is
middleware-untouched; broker service writes richer business-event
rows via `app.services.audit_service.log_business_event`.

Pure ASGI instead of `BaseHTTPMiddleware`: the latter has broken
exception-propagation semantics that defeat FastAPI's exception
handlers for anything that raises from inside a handler. See Starlette
issue #1175 and `docs/prompts/m2-11-fix-asgi-middleware.md`.

Body capture is the one non-trivial wrinkle. ASGI delivers the request
body as one or more `http.request` messages, consumed via the `receive`
callable. Reading them here drains the stream; if we don't replay to
the inner app, the handler's body parse sees nothing and 422s. The
fix: buffer the bytes once, then hand a single-shot replay `receive`
to the inner app.

The audit write lives in a try/finally so an exception from the inner
app doesn't skip the row. The response status we record is whatever
`http.response.start` carried — the exception-handler chain runs
below us and produces the correct status; we just observe.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import parse_qs
from uuid import UUID

from app.database import async_session_factory
from app.models.audit import AuditLog


logger = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────

_UUID_RE = re.compile(
    r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$",
    re.IGNORECASE,
)

_REDACTED_FIELDS: frozenset[str] = frozenset({
    "token_secret",
    "password",
    "secret",
    "api_key",
    "token",
    "access_token",
    "refresh_token",
    "encryption_key",
})
_REDACTED_MARKER = "***REDACTED***"
_MAX_AUDIT_BODY_BYTES = 64 * 1024  # 64 KiB


# ── Helpers ───────────────────────────────────────────────────

def _should_audit(method: str, path: str) -> bool:
    if not path.startswith("/api/v1/"):
        return False
    if path.startswith("/api/v1/me/"):
        return False
    return method in {"POST", "PUT", "DELETE", "PATCH"}


def _extract_resource(path: str) -> tuple[str | None, UUID | None]:
    """From `/api/v1/pools/<uuid>/provision` → (`"pool"`, UUID(...)).

    Returns (None, None) for paths that don't match
    `/api/v1/<noun>[/...]`. Trailing-`s` stripping is a heuristic —
    fine for the M2 resource set; would mangle a non-s-plural.
    """
    parts = path.strip("/").split("/")
    if len(parts) < 3 or parts[0] != "api" or parts[1] != "v1":
        return None, None
    resource_type = parts[2][:-1] if parts[2].endswith("s") else parts[2]
    resource_id: UUID | None = None
    for part in parts[3:]:
        if _UUID_RE.match(part):
            try:
                resource_id = UUID(part)
            except ValueError:
                pass
            break
    return resource_type, resource_id


def _redact(data: Any) -> Any:
    """Recursively replace values whose keys appear in _REDACTED_FIELDS.

    Case-insensitive key comparison; preserves structure; non-dict,
    non-list values pass through unchanged.
    """
    if isinstance(data, dict):
        return {
            k: (
                _REDACTED_MARKER
                if k.lower() in _REDACTED_FIELDS
                else _redact(v)
            )
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_redact(item) for item in data]
    return data


def _get_header(scope: dict, name: str) -> str:
    """Case-insensitive header lookup on an ASGI scope.

    Intentionally duplicated from middleware/auth.py — two copies sits
    below the shared-utility-module threshold; extract on the third.
    """
    target = name.lower().encode("latin-1")
    for key, value in scope.get("headers", []):
        if key.lower() == target:
            return value.decode("latin-1")
    return ""


# ── Middleware ────────────────────────────────────────────────

class AuditMiddleware:
    """Pure-ASGI audit middleware.

        __init__(self, app)
        __call__(self, scope, receive, send)
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path = scope.get("path", "")
        if not _should_audit(method, path):
            await self.app(scope, receive, send)
            return

        # ── Body capture ────────────────────────────────────────
        # ASGI bodies may arrive in multiple http.request messages
        # (chunked uploads). Drain until more_body is False.
        body_chunks: list[bytes] = []
        while True:
            message = await receive()
            msg_type = message.get("type")
            if msg_type == "http.request":
                body_chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    break
            elif msg_type == "http.disconnect":
                # Client gave up before sending the full body. Pass
                # the disconnect through and bail — no audit row.
                async def _passthrough_disconnect(_msg=message):
                    return _msg
                await self.app(scope, _passthrough_disconnect, send)
                return
            else:
                # Unknown message type — bail conservatively.
                break
        body_bytes = b"".join(body_chunks)

        # ── Single-shot replay receive ──────────────────────────
        _body_sent = False

        async def replay_receive() -> dict:
            nonlocal _body_sent
            if not _body_sent:
                _body_sent = True
                return {
                    "type": "http.request",
                    "body": body_bytes,
                    "more_body": False,
                }
            # Post-body reads → disconnect, matching Starlette's
            # exhausted-stream behavior.
            return {"type": "http.disconnect"}

        # ── Wrapped send: observe status, pass everything through ──
        status_code = 500  # default if no http.response.start is sent
        response_started = False

        async def wrapped_send(message: dict) -> None:
            nonlocal status_code, response_started
            if message.get("type") == "http.response.start":
                status_code = message.get("status", 500)
                response_started = True
            await send(message)

        # ── Actor + client info ─────────────────────────────────
        # DevAuthMiddleware (outer) has already set scope["state"]["user"].
        state = scope.get("state") or {}
        user = state.get("user")
        actor = getattr(user, "username", None) or "anonymous"

        client = scope.get("client")
        client_ip = client[0] if client else None

        # ── Call the inner app + write the audit row ────────────
        try:
            await self.app(scope, replay_receive, wrapped_send)
        finally:
            try:
                await _write_audit_row(
                    method=method,
                    path=path,
                    status_code=status_code,
                    body_bytes=body_bytes,
                    content_type=_get_header(scope, "content-type"),
                    query_string=(
                        scope.get("query_string", b"").decode("latin-1")
                    ),
                    actor=actor,
                    client_ip=client_ip,
                )
            except Exception:
                logger.exception(
                    "audit row write failed; request continues"
                )


async def _write_audit_row(
    *,
    method: str,
    path: str,
    status_code: int,
    body_bytes: bytes,
    content_type: str,
    query_string: str,
    actor: str,
    client_ip: str | None,
) -> None:
    resource_type, resource_id = _extract_resource(path)

    query_params_raw = parse_qs(query_string)
    query_params = {
        k: (v[0] if len(v) == 1 else v)
        for k, v in query_params_raw.items()
    }

    details: dict[str, Any] = {
        "status_code": status_code,
        "query_params": query_params,
    }

    if body_bytes and method != "DELETE":
        if "application/json" in content_type:
            if len(body_bytes) > _MAX_AUDIT_BODY_BYTES:
                details["request_body_truncated"] = True
            else:
                try:
                    parsed = json.loads(body_bytes)
                except json.JSONDecodeError:
                    details["request_body_not_json"] = True
                else:
                    details["request_body"] = _redact(parsed)
        else:
            details["request_body_not_json"] = True

    async with async_session_factory() as session:
        session.add(
            AuditLog(
                actor=actor,
                action=f"{method} {path}",
                resource_type=resource_type,
                resource_id=resource_id,
                details=details,
                client_ip=client_ip,
            )
        )
        await session.commit()
