"""Dev-mode header-based auth middleware.

Attaches a `User` to `request.state.user` for every request except a
small bypass list (`/health`, `/docs`, `/openapi.json`, `/redoc`,
`/docs/*`). Missing `X-Dev-User` → 401. Invalid `X-Dev-Role` → 400.
Everything else is permissive (missing groups → empty tuple).

Register in the FastAPI app. Middleware order matters in Starlette
(last-added runs outermost): add this BEFORE the audit middleware so
`request.state.user` is set when audit reads it.

Header → JWT cutover (M4) replaces only this module and
`auth_service.py`'s header parsers; everything downstream keeps its
interface.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.services.auth_service import (
    User,
    parse_groups_header,
    parse_role_header,
)


# Paths that skip auth entirely. FastAPI's /docs subtree mounts
# additional static paths under /docs/; the startswith covers those.
_BYPASS_EXACT: frozenset[str] = frozenset({
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
})


def _is_bypassed(path: str) -> bool:
    return path in _BYPASS_EXACT or path.startswith("/docs/")


class DevAuthMiddleware(BaseHTTPMiddleware):
    """Extract X-Dev-* headers and attach a User to request.state."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint,
    ) -> Response:
        if _is_bypassed(request.url.path):
            return await call_next(request)

        username = request.headers.get("X-Dev-User", "").strip()
        if not username:
            return JSONResponse(
                status_code=401,
                content={
                    "data": None,
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "X-Dev-User header required (M2 dev mode)",
                    },
                },
            )

        try:
            role = parse_role_header(request.headers.get("X-Dev-Role"))
        except ValueError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "data": None,
                    "error": {
                        "code": "INVALID_REQUEST",
                        "message": str(exc),
                    },
                },
            )

        groups = parse_groups_header(request.headers.get("X-Dev-Groups"))
        request.state.user = User(
            username=username, groups=groups, role=role,
        )
        return await call_next(request)
