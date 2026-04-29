"""Dev-mode header-based auth middleware — pure ASGI.

Reads X-Dev-User / X-Dev-Groups / X-Dev-Role from the ASGI scope and
stashes a `User` on `scope["state"]["user"]`. Short-circuits with
401 (missing user) or 400 (malformed role) for non-bypass paths.
Bypasses /health, /docs, /openapi.json, /redoc, and /docs/* with no
auth checks.

Pure ASGI rather than `BaseHTTPMiddleware` — the latter has broken
exception-handling semantics that cause validation errors (and other
handler-raised exceptions) to surface as HTTP 500 even when FastAPI's
exception handlers produce correct responses. See Starlette issue
#1175 and docs/prompts/m2-11-fix-asgi-middleware.md for the details.

Header → JWT cutover (M4) replaces only this module; everything
downstream continues to read `request.state.user` unchanged.
"""
from __future__ import annotations

import json
import logging

from app.services.auth_service import (
    User,
    parse_groups_header,
    parse_role_header,
)
from app.services.jwt_service import InvalidAccessTokenError


logger = logging.getLogger(__name__)


# Paths that bypass auth entirely. M2-10's original list plus
# /api/v1/auth/ (M4-04): the auth endpoints predate authentication
# and must be reachable by unauthenticated clients (otherwise login
# is impossible). Endpoints under /api/v1/auth own their own access
# control via dependency factories (M4-04 dev-mode 503; M4-04 auth
# logic itself).
_BYPASS_PATHS: frozenset[str] = frozenset({
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
})
_BYPASS_PREFIXES: tuple[str, ...] = ("/docs/", "/api/v1/auth/")


def _get_header(scope: dict, name: str) -> str:
    """Fetch a header value from an ASGI scope.

    Returns the empty string if the header is absent. Header names are
    matched case-insensitively (HTTP requirement). Values are decoded
    from raw bytes as latin-1, which is Starlette's internal convention
    — for ASCII values (the normal case for the headers we read) this
    produces the same result as UTF-8; for rare non-ASCII bytes it at
    least preserves them losslessly.
    """
    target = name.lower().encode("latin-1")
    for key, value in scope.get("headers", []):
        if key.lower() == target:
            return value.decode("latin-1")
    return ""


async def _send_json_response(send, status_code: int, body: dict) -> None:
    """Emit a JSONResponse-equivalent via raw ASGI.

    Used when the middleware rejects the request before calling the
    inner app (401 missing user, 400 malformed role).
    """
    payload = json.dumps(body).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status_code,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode("ascii")),
        ],
    })
    await send({
        "type": "http.response.body",
        "body": payload,
        "more_body": False,
    })


class DevAuthMiddleware:
    """Pure-ASGI DevAuth. Shape:

        __init__(self, app)           # Starlette/FastAPI calls this once
        __call__(self, scope, receive, send)  # one per request

    No BaseHTTPMiddleware, no Request/Response objects at the boundary.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive, send) -> None:
        # Only HTTP traffic is subject to auth. Websocket and lifespan
        # scopes pass through unchanged.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if (
            path in _BYPASS_PATHS
            or any(path.startswith(p) for p in _BYPASS_PREFIXES)
        ):
            await self.app(scope, receive, send)
            return

        username = _get_header(scope, "X-Dev-User").strip()
        if not username:
            await _send_json_response(send, 401, {
                "data": None,
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "X-Dev-User header required (M2 dev mode)",
                },
            })
            return

        try:
            role = parse_role_header(_get_header(scope, "X-Dev-Role") or None)
        except ValueError as exc:
            await _send_json_response(send, 400, {
                "data": None,
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": str(exc),
                },
            })
            return

        groups = parse_groups_header(
            _get_header(scope, "X-Dev-Groups") or None
        )

        # FastAPI's `request.state` is a view onto scope["state"]; the
        # State object is initialized on first attribute access. We
        # write to the underlying dict directly so downstream readers
        # (including our AuditMiddleware) see the user immediately.
        state = scope.setdefault("state", {})
        state["user"] = User(
            username=username, groups=groups, role=role,
        )

        await self.app(scope, receive, send)


class JWTAuthMiddleware:
    """Pure-ASGI JWT auth (M4-05). Validates an
    `Authorization: Bearer <access-token>` header on each request, maps
    the validated claims to `User`, and stashes it on
    `scope["state"]["user"]`.

    Bypasses /health, /docs/*, /api/v1/auth/* (login/refresh/logout
    must be reachable unauthenticated). Bypasses websocket and lifespan
    scopes. Returns 401 on missing/malformed/expired/invalid tokens.

    The JWTService is read from `scope["app"].state.jwt_service` per
    request, NOT cached at middleware-construction time. Middleware
    construction happens at app definition (before lifespan runs); the
    service is set by the lifespan handler (M4-04).

    Pure-ASGI (not BaseHTTPMiddleware) for the same reason
    DevAuthMiddleware is — see m2-11-fix-asgi-middleware.md.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if (
            path in _BYPASS_PATHS
            or any(path.startswith(p) for p in _BYPASS_PREFIXES)
        ):
            await self.app(scope, receive, send)
            return

        # 1. Authorization header presence.
        auth_header = _get_header(scope, "Authorization")
        if not auth_header:
            await _send_json_response(send, 401, {
                "data": None,
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "Authorization header required",
                },
            })
            return

        # 2. Parse "Bearer <token>" per RFC 6750 §2.1. Tolerant of
        #    leading/trailing whitespace and case-insensitive scheme.
        scheme, _, token = auth_header.partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token:
            await _send_json_response(send, 401, {
                "data": None,
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "Authorization header must be 'Bearer <token>'",
                },
            })
            return

        # 3. Validate via the JWTService on app.state. Defensive: if
        #    lifespan didn't set it (jwt mode but startup misconfigured),
        #    treat as 503 — the broker is in an invalid state, not the
        #    request.
        app = scope.get("app")
        jwt_service = getattr(app.state, "jwt_service", None) if app else None
        if jwt_service is None:
            logger.error(
                "JWTAuthMiddleware reached without app.state.jwt_service set"
            )
            await _send_json_response(send, 503, {
                "data": None,
                "error": {
                    "code": "SERVICE_UNAVAILABLE",
                    "message": "Authentication service is not initialized",
                },
            })
            return

        try:
            claims = jwt_service.validate_access_token(token)
        except InvalidAccessTokenError:
            # Don't differentiate expired / bad-signature / malformed
            # in the response — same security argument as M4-04's
            # auth endpoints.
            await _send_json_response(send, 401, {
                "data": None,
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "Invalid or expired access token",
                },
            })
            return

        # 4. Construct the User and attach to scope state. The shape
        #    matches M2's DevAuth-produced User exactly so downstream
        #    handlers don't notice the swap.
        state = scope.setdefault("state", {})
        state["user"] = User(
            username=claims.sub,
            groups=claims.groups,
            role=claims.role,
        )

        await self.app(scope, receive, send)
