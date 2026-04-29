"""Unit tests for JWTAuthMiddleware.

Exercises every branch of the middleware: bypass paths, missing
header, malformed scheme, missing service, valid token, expired
token. Uses a minimal Starlette test app with a stub echo handler
that surfaces the User attached by the middleware.

httpx.AsyncClient + ASGITransport invokes the middleware via the same
code path production traffic uses — including header parsing and
scope construction. Starlette's TestClient subtly diverges in scope
shape; the async path is more representative.
"""
from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.middleware.auth import JWTAuthMiddleware
from app.services.jwt_service import (
    AccessTokenClaims,
    InvalidAccessTokenError,
)


# ── Test app factory ─────────────────────────────────────────


def _make_app(jwt_service=None) -> Starlette:
    """Construct a minimal Starlette app with the middleware mounted
    and `app.state.jwt_service` set (or omitted, for the
    misconfigured-503 case)."""

    async def echo(request: Request) -> JSONResponse:
        user = getattr(request.state, "user", None)
        return JSONResponse({
            "username": user.username if user else None,
            "groups": list(user.groups) if user else None,
            "role": user.role if user else None,
        })

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def login(_: Request) -> JSONResponse:
        return JSONResponse({"data": "login"})

    app = Starlette(routes=[
        Route("/echo", echo),
        Route("/health", health),
        Route("/api/v1/auth/login", login, methods=["POST"]),
    ])
    app.add_middleware(JWTAuthMiddleware)
    if jwt_service is not None:
        app.state.jwt_service = jwt_service
    return app


def _stub_claims(
    sub: str = "alice",
    groups: tuple[str, ...] = (),
    role: str = "user",
) -> AccessTokenClaims:
    """Construct an AccessTokenClaims with all fields populated."""
    return AccessTokenClaims(
        sub=sub,
        groups=groups,
        role=role,
        iat=0,
        exp=9_999_999_999,
        jti=uuid4(),
    )


# ── Bypass paths ─────────────────────────────────────────────


async def test_health_bypasses_auth():
    """/health reaches the handler without a token."""
    jwt_svc = MagicMock()
    app = _make_app(jwt_svc)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as c:
        r = await c.get("/health")
    assert r.status_code == 200
    jwt_svc.validate_access_token.assert_not_called()


async def test_auth_endpoints_bypass():
    """/api/v1/auth/login reaches the handler without a token."""
    jwt_svc = MagicMock()
    app = _make_app(jwt_svc)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as c:
        r = await c.post("/api/v1/auth/login")
    assert r.status_code == 200
    jwt_svc.validate_access_token.assert_not_called()


# ── 401 paths ────────────────────────────────────────────────


async def test_missing_authorization_header_is_401():
    app = _make_app(MagicMock())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as c:
        r = await c.get("/echo")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


async def test_malformed_scheme_is_401():
    """`Authorization: Basic abc` is rejected — only Bearer is accepted."""
    app = _make_app(MagicMock())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as c:
        r = await c.get("/echo", headers={"Authorization": "Basic abc"})
    assert r.status_code == 401


async def test_empty_bearer_token_is_401():
    """`Authorization: Bearer  ` (with empty token) is rejected."""
    app = _make_app(MagicMock())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as c:
        r = await c.get("/echo", headers={"Authorization": "Bearer "})
    assert r.status_code == 401


async def test_invalid_token_is_401():
    """JWTService raises InvalidAccessTokenError → 401."""
    jwt_svc = MagicMock()
    jwt_svc.validate_access_token.side_effect = InvalidAccessTokenError(
        "expired"
    )
    app = _make_app(jwt_svc)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as c:
        r = await c.get(
            "/echo",
            headers={"Authorization": "Bearer broken.token.here"},
        )
    assert r.status_code == 401
    jwt_svc.validate_access_token.assert_called_once_with("broken.token.here")


async def test_bearer_scheme_case_insensitive():
    """`Authorization: bearer ...` (lowercase) works per RFC 6750."""
    jwt_svc = MagicMock()
    jwt_svc.validate_access_token.return_value = _stub_claims()
    app = _make_app(jwt_svc)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as c:
        r = await c.get("/echo", headers={"Authorization": "bearer xyz"})
    assert r.status_code == 200


# ── Misconfigured (no JWTService on app.state) ──────────────


async def test_missing_jwt_service_is_503():
    """If app.state.jwt_service is unset (broker bootstrap bug), 503."""
    app = _make_app(jwt_service=None)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as c:
        r = await c.get("/echo", headers={"Authorization": "Bearer xyz"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


# ── Happy path ──────────────────────────────────────────────


async def test_valid_token_attaches_user():
    """Valid token → request.state.user populated from claims."""
    jwt_svc = MagicMock()
    jwt_svc.validate_access_token.return_value = _stub_claims(
        sub="alice",
        groups=("Engineering", "VPN-Users"),
        role="user",
    )
    app = _make_app(jwt_svc)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as c:
        r = await c.get("/echo", headers={"Authorization": "Bearer xyz"})
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "alice"
    assert body["groups"] == ["Engineering", "VPN-Users"]
    assert body["role"] == "user"


async def test_admin_role_propagates():
    jwt_svc = MagicMock()
    jwt_svc.validate_access_token.return_value = _stub_claims(role="admin")
    app = _make_app(jwt_svc)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as c:
        r = await c.get("/echo", headers={"Authorization": "Bearer xyz"})
    assert r.json()["role"] == "admin"


async def test_token_passed_unmodified_to_validator():
    """Whitespace stripping happens before the validator sees the token."""
    jwt_svc = MagicMock()
    jwt_svc.validate_access_token.return_value = _stub_claims()
    app = _make_app(jwt_svc)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as c:
        await c.get(
            "/echo",
            headers={"Authorization": "Bearer   abc.def.ghi  "},
        )
    jwt_svc.validate_access_token.assert_called_once_with("abc.def.ghi")
