"""Tests for BrokerClient — verb helpers, envelope unwrapping,
401-replay, transport-failure handling."""
from __future__ import annotations

import httpx
import pytest

from openvdi_admin.auth import BrokerAuthClient
from openvdi_admin.client import BrokerClient
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


@pytest.fixture
async def auth_logged_in(settings, mock_broker):
    """An auth client that's already logged in with tok-1."""
    mock_broker.post("/api/v1/auth/login").respond(json=_login_response("tok-1"))
    auth = BrokerAuthClient(settings)
    await auth.get_token()
    yield auth
    await auth.close()


class TestVerbHelpers:
    async def test_get_returns_unwrapped_data(
        self, settings, mock_broker, auth_logged_in,
    ):
        mock_broker.get("/api/v1/clusters").respond(
            json={"data": [{"id": "c1"}], "error": None},
        )
        client = BrokerClient(auth_logged_in, settings)
        try:
            result = await client.get("/api/v1/clusters")
            assert result == [{"id": "c1"}]
        finally:
            await client.close()

    async def test_get_with_params(
        self, settings, mock_broker, auth_logged_in,
    ):
        route = mock_broker.get("/api/v1/desktops").respond(
            json={"data": [], "error": None},
        )
        client = BrokerClient(auth_logged_in, settings)
        try:
            await client.get(
                "/api/v1/desktops",
                params={"pool_id": "abc", "status": "available"},
            )
            assert route.called
            assert route.calls.last.request.url.params["pool_id"] == "abc"
            assert route.calls.last.request.url.params["status"] == "available"
        finally:
            await client.close()

    async def test_post_with_body_serializes_json(
        self, settings, mock_broker, auth_logged_in,
    ):
        route = mock_broker.post("/api/v1/clusters").respond(
            json={"data": {"id": "new"}, "error": None},
        )
        client = BrokerClient(auth_logged_in, settings)
        try:
            await client.post(
                "/api/v1/clusters",
                body={"name": "test", "api_url": "https://x:8006"},
            )
            assert route.called
            payload = route.calls.last.request.read()
            assert b"\"name\":\"test\"" in payload or b'"name": "test"' in payload
        finally:
            await client.close()

    async def test_post_no_body_sends_no_json(
        self, settings, mock_broker, auth_logged_in,
    ):
        """POST /pools/{id}/drain takes no body."""
        route = mock_broker.post("/api/v1/pools/abc/drain").respond(
            json={"data": {"ok": True}, "error": None},
        )
        client = BrokerClient(auth_logged_in, settings)
        try:
            await client.post("/api/v1/pools/abc/drain")
            assert route.called
            # No JSON content-type header sent (httpx omits it when
            # json=None).
            request = route.calls.last.request
            assert request.headers.get("content-type") is None
        finally:
            await client.close()

    async def test_put_sends_body(
        self, settings, mock_broker, auth_logged_in,
    ):
        route = mock_broker.put("/api/v1/clusters/c1").respond(
            json={"data": {"id": "c1", "name": "renamed"}, "error": None},
        )
        client = BrokerClient(auth_logged_in, settings)
        try:
            await client.put("/api/v1/clusters/c1", body={"name": "renamed"})
            assert route.called
        finally:
            await client.close()

    async def test_delete_204_returns_none(
        self, settings, mock_broker, auth_logged_in,
    ):
        mock_broker.delete("/api/v1/sessions/s1").respond(status_code=204)
        client = BrokerClient(auth_logged_in, settings)
        try:
            result = await client.delete("/api/v1/sessions/s1")
            assert result is None
        finally:
            await client.close()

    async def test_authorization_header_attached(
        self, settings, mock_broker, auth_logged_in,
    ):
        route = mock_broker.get("/api/v1/clusters").respond(
            json={"data": [], "error": None},
        )
        client = BrokerClient(auth_logged_in, settings)
        try:
            await client.get("/api/v1/clusters")
            request = route.calls.last.request
            assert request.headers["authorization"] == "Bearer tok-1"
        finally:
            await client.close()


class TestGetRaw:
    """get_raw skips envelope unwrapping. /health is the canonical
    consumer (M4-12 returns plain `{"status": "ok"}`, not enveloped)."""

    async def test_returns_raw_payload(
        self, settings, mock_broker, auth_logged_in,
    ):
        mock_broker.get("/health").respond(
            json={"status": "ok", "version": "0.5.0"},
        )
        client = BrokerClient(auth_logged_in, settings)
        try:
            result = await client.get_raw("/health")
            assert result == {"status": "ok", "version": "0.5.0"}
        finally:
            await client.close()

    async def test_does_not_unwrap_envelope_shape(
        self, settings, mock_broker, auth_logged_in,
    ):
        # Even when the broker happens to return an enveloped
        # response on a get_raw call, we hand it back as-is.
        mock_broker.get("/health").respond(
            json={"data": {"status": "ok"}, "error": None},
        )
        client = BrokerClient(auth_logged_in, settings)
        try:
            result = await client.get_raw("/health")
            assert result == {
                "data": {"status": "ok"}, "error": None,
            }
        finally:
            await client.close()

    async def test_4xx_raises_http_error(
        self, settings, mock_broker, auth_logged_in,
    ):
        mock_broker.get("/health").respond(status_code=503)
        client = BrokerClient(auth_logged_in, settings)
        try:
            with pytest.raises(BrokerError) as exc:
                await client.get_raw("/health")
            assert exc.value.http_status == 503
            assert exc.value.code == "HTTP_ERROR"
        finally:
            await client.close()

    async def test_attaches_authorization_header(
        self, settings, mock_broker, auth_logged_in,
    ):
        route = mock_broker.get("/health").respond(
            json={"status": "ok"},
        )
        client = BrokerClient(auth_logged_in, settings)
        try:
            await client.get_raw("/health")
            request = route.calls.last.request
            assert request.headers["authorization"] == "Bearer tok-1"
        finally:
            await client.close()


class TestEnvelopeFailures:
    async def test_2xx_with_error_envelope_raises(
        self, settings, mock_broker, auth_logged_in,
    ):
        mock_broker.get("/api/v1/clusters").respond(
            json={"data": None, "error": {
                "code": "INTERNAL_ERROR", "message": "weird state",
            }},
        )
        client = BrokerClient(auth_logged_in, settings)
        try:
            with pytest.raises(BrokerError) as exc:
                await client.get("/api/v1/clusters")
            assert exc.value.code == "INTERNAL_ERROR"
        finally:
            await client.close()

    async def test_4xx_envelope_raises(
        self, settings, mock_broker, auth_logged_in,
    ):
        mock_broker.delete("/api/v1/clusters/c1").respond(
            status_code=409,
            json={"data": None, "error": {
                "code": "CONFLICT",
                "message": "pools reference cluster",
            }},
        )
        client = BrokerClient(auth_logged_in, settings)
        try:
            with pytest.raises(BrokerError) as exc:
                await client.delete("/api/v1/clusters/c1")
            assert exc.value.code == "CONFLICT"
            assert exc.value.http_status == 409
        finally:
            await client.close()

    async def test_non_json_5xx_raises_internal(
        self, settings, mock_broker, auth_logged_in,
    ):
        mock_broker.get("/api/v1/clusters").respond(
            status_code=502,
            content=b"<html>Bad Gateway</html>",
            headers={"Content-Type": "text/html"},
        )
        client = BrokerClient(auth_logged_in, settings)
        try:
            with pytest.raises(BrokerError) as exc:
                await client.get("/api/v1/clusters")
            assert exc.value.http_status == 502
            assert exc.value.code == "INTERNAL_ERROR"
        finally:
            await client.close()


class TestReplay:
    async def test_401_triggers_refresh_and_replay(
        self, settings, mock_broker,
    ):
        # Initial login.
        mock_broker.post("/api/v1/auth/login").respond(
            json=_login_response("tok-old"),
        )
        # Refresh issues new token.
        mock_broker.post("/api/v1/auth/refresh").respond(
            json=_login_response("tok-new"),
        )
        # First GET 401s; second GET (after refresh) 200s.
        get_route = mock_broker.get("/api/v1/clusters").mock(
            side_effect=[
                httpx.Response(
                    status_code=401,
                    json={"data": None, "error": {
                        "code": "UNAUTHORIZED", "message": "expired",
                    }},
                ),
                httpx.Response(
                    status_code=200,
                    json={"data": [{"id": "c1"}], "error": None},
                ),
            ],
        )
        auth = BrokerAuthClient(settings)
        client = BrokerClient(auth, settings)
        try:
            result = await client.get("/api/v1/clusters")
            assert result == [{"id": "c1"}]
            assert get_route.call_count == 2
            # First request used tok-old; second used tok-new.
            assert (
                get_route.calls[0].request.headers["authorization"]
                == "Bearer tok-old"
            )
            assert (
                get_route.calls[1].request.headers["authorization"]
                == "Bearer tok-new"
            )
        finally:
            await client.close()
            await auth.close()

    async def test_401_then_replay_401_raises(
        self, settings, mock_broker,
    ):
        """If both the original AND replay request 401, the second 401
        propagates as a BrokerError. No second refresh attempt."""
        mock_broker.post("/api/v1/auth/login").respond(
            json=_login_response("tok-1"),
        )
        mock_broker.post("/api/v1/auth/refresh").respond(
            json=_login_response("tok-2"),
        )
        get_route = mock_broker.get("/api/v1/clusters").respond(
            status_code=401,
            json={"data": None, "error": {
                "code": "UNAUTHORIZED", "message": "still bad",
            }},
        )
        auth = BrokerAuthClient(settings)
        client = BrokerClient(auth, settings)
        try:
            with pytest.raises(BrokerError) as exc:
                await client.get("/api/v1/clusters")
            assert exc.value.http_status == 401
            # Original + one replay = 2 calls. No third attempt.
            assert get_route.call_count == 2
        finally:
            await client.close()
            await auth.close()

    async def test_401_with_refresh_failure_propagates(
        self, settings, mock_broker,
    ):
        mock_broker.post("/api/v1/auth/login").respond(
            json=_login_response("tok-1"),
        )
        mock_broker.post("/api/v1/auth/refresh").respond(
            status_code=500,
            json={"data": None, "error": {
                "code": "INTERNAL_ERROR", "message": "broker down",
            }},
        )
        mock_broker.get("/api/v1/clusters").respond(
            status_code=401,
            json={"data": None, "error": {"code": "UNAUTHORIZED", "message": "x"}},
        )
        auth = BrokerAuthClient(settings)
        client = BrokerClient(auth, settings)
        try:
            with pytest.raises(BrokerError) as exc:
                await client.get("/api/v1/clusters")
            assert exc.value.code == "REFRESH_FAILED"
        finally:
            await client.close()
            await auth.close()


class TestTransportFailures:
    async def test_network_error_raises_transport(
        self, settings, mock_broker, auth_logged_in,
    ):
        mock_broker.get("/api/v1/clusters").mock(
            side_effect=httpx.ConnectError("connection refused"),
        )
        client = BrokerClient(auth_logged_in, settings)
        try:
            with pytest.raises(BrokerError) as exc:
                await client.get("/api/v1/clusters")
            assert exc.value.code == "TRANSPORT_ERROR"
            assert exc.value.http_status == 0
        finally:
            await client.close()
