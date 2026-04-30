"""Tests for RequestIdMiddleware via the M4-05 ASGI test pattern.

httpx.AsyncClient + ASGITransport invokes the middleware via the same
code path production traffic uses, including header parsing and scope
construction.
"""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.logging import current_request_id_var
from app.middleware.request_id import RequestIdMiddleware


def _make_app() -> Starlette:
    """Test app with one route that returns the current ContextVar
    value — lets tests verify the middleware set it.
    """
    async def echo(request):
        return JSONResponse({
            "request_id": current_request_id_var.get(),
        })

    app = Starlette(routes=[Route("/echo", echo)])
    app.add_middleware(RequestIdMiddleware)
    return app


# ── Tests ────────────────────────────────────────────────────


async def test_generates_uuid_when_no_header():
    app = _make_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as c:
        r = await c.get("/echo")
    body = r.json()
    assert body["request_id"]
    # Looks like a UUID (8-4-4-4-12 hex format).
    assert len(body["request_id"]) == 36
    assert body["request_id"].count("-") == 4
    # Echoed in response header.
    assert r.headers.get("x-request-id") == body["request_id"]


async def test_preserves_client_supplied_header():
    app = _make_app()
    custom_id = "client-correlation-12345"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as c:
        r = await c.get("/echo", headers={"x-request-id": custom_id})
    body = r.json()
    assert body["request_id"] == custom_id
    assert r.headers.get("x-request-id") == custom_id


async def test_rejects_too_long_header():
    """Client sends a 200-char id; middleware ignores and generates fresh."""
    app = _make_app()
    long_id = "x" * 200
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as c:
        r = await c.get("/echo", headers={"x-request-id": long_id})
    body = r.json()
    # Generated fresh — not the client's value.
    assert body["request_id"] != long_id
    assert len(body["request_id"]) == 36  # UUID format


async def test_empty_header_falls_back_to_generation():
    """Empty value triggers generation path."""
    app = _make_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as c:
        r = await c.get("/echo", headers={"x-request-id": ""})
    body = r.json()
    assert body["request_id"]
    assert len(body["request_id"]) == 36


async def test_strips_whitespace_from_client_header():
    app = _make_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as c:
        r = await c.get(
            "/echo", headers={"x-request-id": "  custom  "},
        )
    body = r.json()
    assert body["request_id"] == "custom"


async def test_contextvar_reset_after_request():
    """After the request finishes, the ContextVar is back to None
    (no leak across requests).
    """
    app = _make_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as c:
        await c.get("/echo")
    # We're outside the request scope; ContextVar default should hold.
    assert current_request_id_var.get() is None


async def test_multiple_requests_get_distinct_ids():
    app = _make_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as c:
        a = (await c.get("/echo")).json()["request_id"]
        b = (await c.get("/echo")).json()["request_id"]
    assert a != b
