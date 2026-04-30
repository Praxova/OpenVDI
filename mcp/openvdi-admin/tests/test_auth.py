"""Tests for BrokerAuthClient — lazy login, refresh-cookie flow,
concurrent dedup, login-fallback on refresh failure."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from openvdi_admin.auth import BrokerAuthClient
from openvdi_admin.errors import BrokerError


def _login_response(token: str = "tok-1") -> dict:
    return {
        "data": {
            "access_token": token,
            "expires_in": 900,
            "role": "admin",
        },
        "error": None,
    }


class TestLazyLogin:
    async def test_first_call_logs_in(self, settings, mock_broker):
        mock_broker.post("/api/v1/auth/login").respond(json=_login_response())
        client = BrokerAuthClient(settings)
        try:
            assert await client.get_token() == "tok-1"
        finally:
            await client.close()

    async def test_subsequent_calls_reuse_token(self, settings, mock_broker):
        login_route = mock_broker.post("/api/v1/auth/login").respond(
            json=_login_response(),
        )
        client = BrokerAuthClient(settings)
        try:
            await client.get_token()
            await client.get_token()
            await client.get_token()
            assert login_route.call_count == 1
        finally:
            await client.close()

    async def test_concurrent_first_callers_share_one_login(
        self, settings, mock_broker,
    ):
        """All 10 concurrent callers should converge on a single login.
        The first inside the lock does the work; the rest see the
        populated token."""
        login_route = mock_broker.post("/api/v1/auth/login").respond(
            json=_login_response(),
        )
        client = BrokerAuthClient(settings)
        try:
            results = await asyncio.gather(
                *[client.get_token() for _ in range(10)],
            )
            assert all(r == "tok-1" for r in results)
            assert login_route.call_count == 1
        finally:
            await client.close()


class TestForceNewToken:
    async def test_refresh_succeeds(self, settings, mock_broker):
        mock_broker.post("/api/v1/auth/login").respond(
            json=_login_response("tok-1"),
        )
        mock_broker.post("/api/v1/auth/refresh").respond(
            json=_login_response("tok-2"),
        )
        client = BrokerAuthClient(settings)
        try:
            await client.get_token()  # → tok-1
            new = await client.force_new_token()
            assert new == "tok-2"
        finally:
            await client.close()

    async def test_refresh_401_falls_back_to_login(
        self, settings, mock_broker,
    ):
        login_route = mock_broker.post("/api/v1/auth/login").respond(
            json=_login_response("tok-fresh"),
        )
        mock_broker.post("/api/v1/auth/refresh").respond(
            status_code=401,
            json={"data": None, "error": {
                "code": "UNAUTHORIZED", "message": "expired",
            }},
        )
        client = BrokerAuthClient(settings)
        try:
            await client.get_token()
            new = await client.force_new_token()
            assert new == "tok-fresh"
            # One initial login + one re-login after refresh-failed = 2.
            assert login_route.call_count == 2
        finally:
            await client.close()

    async def test_refresh_500_propagates_without_login_fallback(
        self, settings, mock_broker,
    ):
        login_route = mock_broker.post("/api/v1/auth/login").respond(
            json=_login_response("tok-1"),
        )
        mock_broker.post("/api/v1/auth/refresh").respond(
            status_code=500,
            json={"data": None, "error": {
                "code": "INTERNAL_ERROR", "message": "broker down",
            }},
        )
        client = BrokerAuthClient(settings)
        try:
            await client.get_token()
            with pytest.raises(BrokerError) as exc:
                await client.force_new_token()
            assert exc.value.code == "REFRESH_FAILED"
            assert exc.value.http_status == 500
            # Only the initial login — 5xx on refresh does NOT trigger
            # a re-login (5xx isn't "creds invalid", it's "broker
            # broken"). Per the design notes.
            assert login_route.call_count == 1
        finally:
            await client.close()

    async def test_concurrent_refresh_dedup(self, settings, mock_broker):
        """Many concurrent 401s coalesce onto one refresh call."""
        mock_broker.post("/api/v1/auth/login").respond(
            json=_login_response("tok-1"),
        )
        refresh_route = mock_broker.post("/api/v1/auth/refresh").respond(
            json=_login_response("tok-2"),
        )
        client = BrokerAuthClient(settings)
        try:
            await client.get_token()
            results = await asyncio.gather(
                *[client.force_new_token() for _ in range(10)],
            )
            assert all(r == "tok-2" for r in results)
            assert refresh_route.call_count == 1
        finally:
            await client.close()

    async def test_subsequent_force_after_refresh_done_reruns(
        self, settings, mock_broker,
    ):
        """Once a refresh promise resolves, the NEXT force_new_token
        call starts a fresh refresh (not a stale-promise reuse)."""
        mock_broker.post("/api/v1/auth/login").respond(
            json=_login_response("tok-1"),
        )
        refresh_route = mock_broker.post("/api/v1/auth/refresh").respond(
            json=_login_response("tok-2"),
        )
        client = BrokerAuthClient(settings)
        try:
            await client.get_token()
            assert await client.force_new_token() == "tok-2"
            assert await client.force_new_token() == "tok-2"
            # Two separate refresh calls — the first promise was done.
            assert refresh_route.call_count == 2
        finally:
            await client.close()


class TestLoginFailure:
    async def test_transport_failure_raises_transport_error(
        self, settings, mock_broker,
    ):
        mock_broker.post("/api/v1/auth/login").mock(
            side_effect=httpx.ConnectError("dns failed"),
        )
        client = BrokerAuthClient(settings)
        try:
            with pytest.raises(BrokerError) as exc:
                await client.get_token()
            assert exc.value.code == "TRANSPORT_ERROR"
            assert exc.value.http_status == 0
        finally:
            await client.close()

    async def test_login_401_propagates(self, settings, mock_broker):
        mock_broker.post("/api/v1/auth/login").respond(
            status_code=401,
            json={"data": None, "error": {
                "code": "UNAUTHORIZED", "message": "bad creds",
            }},
        )
        client = BrokerAuthClient(settings)
        try:
            with pytest.raises(BrokerError) as exc:
                await client.get_token()
            assert exc.value.code == "LOGIN_FAILED"
            assert exc.value.http_status == 401
        finally:
            await client.close()

    async def test_login_response_missing_token_raises(
        self, settings, mock_broker,
    ):
        mock_broker.post("/api/v1/auth/login").respond(
            json={"data": {}, "error": None},
        )
        client = BrokerAuthClient(settings)
        try:
            with pytest.raises(BrokerError) as exc:
                await client.get_token()
            assert exc.value.code == "LOGIN_FAILED"
            assert "missing access_token" in exc.value.message
        finally:
            await client.close()


class TestRequestIdHeader:
    """X-Request-ID propagation on auth endpoints. Login + refresh
    inherit the request_id from the calling tool's ContextVar so the
    broker sees one UUID across the full tool invocation."""

    async def test_login_attaches_header_when_set(
        self, settings, mock_broker,
    ):
        from openvdi_admin._request_context import (
            clear_request_id, new_request_id,
        )

        clear_request_id()
        rid = new_request_id()
        try:
            route = mock_broker.post("/api/v1/auth/login").respond(
                json={
                    "data": {
                        "access_token": "tok-1",
                        "expires_in": 900,
                        "role": "admin",
                    },
                    "error": None,
                },
            )
            client = BrokerAuthClient(settings)
            try:
                await client.get_token()
                req = route.calls.last.request
                assert req.headers.get("x-request-id") == rid
            finally:
                await client.close()
        finally:
            clear_request_id()

    async def test_login_skips_header_when_unset(
        self, settings, mock_broker,
    ):
        from openvdi_admin._request_context import clear_request_id

        clear_request_id()
        route = mock_broker.post("/api/v1/auth/login").respond(
            json={
                "data": {
                    "access_token": "tok-1",
                    "expires_in": 900,
                    "role": "admin",
                },
                "error": None,
            },
        )
        client = BrokerAuthClient(settings)
        try:
            await client.get_token()
            req = route.calls.last.request
            assert "x-request-id" not in (
                k.lower() for k in req.headers.keys()
            )
        finally:
            await client.close()

    async def test_refresh_attaches_header(
        self, settings, mock_broker,
    ):
        from openvdi_admin._request_context import (
            clear_request_id, new_request_id,
        )

        clear_request_id()
        # Initial login (no request_id), then refresh (with request_id).
        mock_broker.post("/api/v1/auth/login").respond(
            json={
                "data": {
                    "access_token": "tok-1",
                    "expires_in": 900,
                    "role": "admin",
                },
                "error": None,
            },
        )
        refresh_route = mock_broker.post(
            "/api/v1/auth/refresh",
        ).respond(
            json={
                "data": {
                    "access_token": "tok-2",
                    "expires_in": 900,
                    "role": "admin",
                },
                "error": None,
            },
        )
        client = BrokerAuthClient(settings)
        try:
            await client.get_token()  # login (no rid)
            rid = new_request_id()
            try:
                await client.force_new_token()
                req = refresh_route.calls.last.request
                assert req.headers.get("x-request-id") == rid
            finally:
                clear_request_id()
        finally:
            await client.close()
