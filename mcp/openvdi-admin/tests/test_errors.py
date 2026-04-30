"""Tests for envelope unwrapping + BrokerError ergonomics."""
from __future__ import annotations

import pytest

from openvdi_admin.errors import BrokerError, unwrap_envelope


class TestUnwrapEnvelope:
    def test_success_returns_data(self):
        assert unwrap_envelope(
            {"data": {"id": 1}, "error": None}, 200,
        ) == {"id": 1}

    def test_success_with_list(self):
        assert unwrap_envelope({"data": [], "error": None}, 200) == []

    def test_success_with_null_data_returns_none(self):
        # 2xx with explicitly-null data is uncommon but valid.
        assert unwrap_envelope({"data": None, "error": None}, 200) is None

    def test_envelope_with_error_raises(self):
        with pytest.raises(BrokerError) as exc:
            unwrap_envelope(
                {
                    "data": None,
                    "error": {"code": "POOL_FULL", "message": "no spares"},
                },
                503,
            )
        assert exc.value.code == "POOL_FULL"
        assert exc.value.http_status == 503
        assert exc.value.message == "no spares"

    def test_envelope_with_details_preserves_them(self):
        with pytest.raises(BrokerError) as exc:
            unwrap_envelope(
                {
                    "data": None,
                    "error": {
                        "code": "PROVIDER_ERROR",
                        "message": "clone failed",
                        "details": {"raw": "storage full"},
                    },
                },
                502,
            )
        assert exc.value.details == {"raw": "storage full"}

    def test_non_dict_details_dropped_defensively(self):
        """Malformed `details: "string"` shouldn't leak into the
        BrokerError; keep details=None when not a dict."""
        with pytest.raises(BrokerError) as exc:
            unwrap_envelope(
                {
                    "data": None,
                    "error": {
                        "code": "X",
                        "message": "y",
                        "details": "not a dict",
                    },
                },
                500,
            )
        assert exc.value.details is None

    def test_non_envelope_dict_raises_internal(self):
        with pytest.raises(BrokerError) as exc:
            unwrap_envelope({"foo": "bar"}, 200)
        assert exc.value.code == "INTERNAL_ERROR"

    def test_non_dict_raises_internal(self):
        with pytest.raises(BrokerError) as exc:
            unwrap_envelope("string-not-dict", 200)
        assert exc.value.code == "INTERNAL_ERROR"

    def test_envelope_with_non_dict_error_raises_internal(self):
        with pytest.raises(BrokerError) as exc:
            unwrap_envelope({"data": None, "error": "string"}, 500)
        assert exc.value.code == "INTERNAL_ERROR"

    def test_missing_code_falls_back_to_internal(self):
        with pytest.raises(BrokerError) as exc:
            unwrap_envelope(
                {"data": None, "error": {"message": "no code field"}},
                500,
            )
        assert exc.value.code == "INTERNAL_ERROR"
        assert exc.value.message == "no code field"


class TestBrokerError:
    def test_str_includes_code_and_status(self):
        err = BrokerError(
            http_status=503, code="POOL_FULL", message="no spares",
        )
        s = str(err)
        assert "POOL_FULL" in s
        assert "503" in s
        assert "no spares" in s

    def test_transport_classmethod(self):
        err = BrokerError.transport("connection refused")
        assert err.code == "TRANSPORT_ERROR"
        assert err.http_status == 0
        assert err.message == "connection refused"

    def test_envelope_missing_classmethod(self):
        err = BrokerError.envelope_missing(502)
        assert err.code == "INTERNAL_ERROR"
        assert err.http_status == 502
        assert "502" in err.message

    def test_is_exception(self):
        # Sanity — BrokerError participates in the normal exception
        # machinery (raise / except / chained).
        with pytest.raises(BrokerError):
            raise BrokerError(http_status=400, code="X", message="y")
