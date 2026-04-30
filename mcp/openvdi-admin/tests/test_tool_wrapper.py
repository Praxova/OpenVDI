"""instrument_tool decorator behavior."""
from __future__ import annotations

import logging

import pytest

from openvdi_admin._request_context import (
    clear_request_id,
    current_request_id,
)
from openvdi_admin._tool_wrapper import (
    _result_envelope_ok,
    instrument_tool,
)
from openvdi_admin.errors import BrokerError


@pytest.fixture(autouse=True)
def _isolate_context():
    clear_request_id()
    yield
    clear_request_id()


class TestSuccessfulInvocation:
    async def test_returns_underlying_value(self):
        @instrument_tool
        async def fn(x: int) -> int:
            return x * 2

        result = await fn(5)
        assert result == 10

    async def test_emits_completion_log_with_extras(self, caplog):
        @instrument_tool
        async def openvdi_thing() -> dict:
            return {"data": "hello"}

        with caplog.at_level(
            logging.INFO, logger="openvdi_admin._tool_wrapper",
        ):
            await openvdi_thing()

        completion = next(
            r for r in caplog.records if "completed" in r.message
        )
        assert completion.tool == "openvdi_thing"
        assert completion.outcome == "ok"
        assert isinstance(completion.duration_ms, int)
        assert completion.duration_ms >= 0
        assert completion.result_envelope_ok is None

    async def test_intent_envelope_ok_true_logged(self, caplog):
        @instrument_tool
        async def openvdi_intent() -> dict:
            return {"ok": True, "operation": "x"}

        with caplog.at_level(
            logging.INFO, logger="openvdi_admin._tool_wrapper",
        ):
            await openvdi_intent()

        rec = next(
            r for r in caplog.records if "completed" in r.message
        )
        assert rec.result_envelope_ok is True

    async def test_intent_envelope_ok_false_logged(self, caplog):
        """An intent tool that returns ok=false (structured failure)
        is still a successful Python call — outcome=ok, but the
        envelope_ok=False makes the failure visible to operators."""
        @instrument_tool
        async def openvdi_intent_failed() -> dict:
            return {"ok": False, "error_code": "CONFLICT"}

        with caplog.at_level(
            logging.INFO, logger="openvdi_admin._tool_wrapper",
        ):
            await openvdi_intent_failed()

        rec = next(
            r for r in caplog.records if "completed" in r.message
        )
        assert rec.outcome == "ok"
        assert rec.result_envelope_ok is False


class TestExceptionPath:
    async def test_brokererror_logged_without_traceback(self, caplog):
        @instrument_tool
        async def openvdi_bad() -> int:
            raise BrokerError(
                http_status=409, code="CONFLICT", message="busy",
            )

        with caplog.at_level(
            logging.ERROR, logger="openvdi_admin._tool_wrapper",
        ):
            with pytest.raises(BrokerError):
                await openvdi_bad()

        err = next(r for r in caplog.records if r.levelname == "ERROR")
        assert err.tool == "openvdi_bad"
        assert err.outcome == "error"
        assert err.exception_code == "CONFLICT"
        assert err.exception_type == "BrokerError"
        # exc_info=False per the design — BrokerError is structured;
        # traceback is noise. Logger normalizes the False to a falsy
        # value rather than None, hence `not err.exc_info`.
        assert not err.exc_info

    async def test_unexpected_exception_includes_traceback(
        self, caplog,
    ):
        @instrument_tool
        async def openvdi_buggy() -> int:
            raise ValueError("oops")

        with caplog.at_level(
            logging.ERROR, logger="openvdi_admin._tool_wrapper",
        ):
            with pytest.raises(ValueError):
                await openvdi_buggy()

        err = next(r for r in caplog.records if r.levelname == "ERROR")
        assert err.exception_type == "ValueError"
        assert err.exception_message == "oops"
        # Unexpected → traceback captured for debugging.
        assert err.exc_info is not None


class TestRequestIdLifecycle:
    async def test_request_id_set_during_tool_body(self):
        captured: list[str | None] = []

        @instrument_tool
        async def fn() -> None:
            captured.append(current_request_id())

        await fn()
        assert captured[0] is not None
        assert len(captured[0]) == 36

    async def test_log_record_carries_request_id(self, caplog):
        @instrument_tool
        async def openvdi_thing() -> int:
            return 1

        with caplog.at_level(
            logging.INFO, logger="openvdi_admin._tool_wrapper",
        ):
            await openvdi_thing()

        rec = next(
            r for r in caplog.records if "completed" in r.message
        )
        assert isinstance(rec.request_id, str)
        assert len(rec.request_id) == 36


class TestFunctoolsWraps:
    """The wrapper must preserve __name__ and __doc__ so FastMCP
    introspection (and human-facing tool listings) sees the original
    tool, not the wrapper."""

    async def test_preserves_name(self):
        @instrument_tool
        async def openvdi_specific_name() -> int:
            return 1

        assert openvdi_specific_name.__name__ == (
            "openvdi_specific_name"
        )

    async def test_preserves_docstring(self):
        @instrument_tool
        async def openvdi_documented() -> int:
            """The original docstring."""
            return 1

        assert openvdi_documented.__doc__ == "The original docstring."


class TestResultEnvelopeOk:
    def test_intent_success_envelope(self):
        assert _result_envelope_ok({"ok": True, "data": "..."}) is True

    def test_intent_failure_envelope(self):
        assert _result_envelope_ok(
            {"ok": False, "error_code": "X"},
        ) is False

    def test_thin_wrapper_dict_no_ok_key(self):
        assert _result_envelope_ok({"data": [1, 2]}) is None

    def test_list_returns_none(self):
        assert _result_envelope_ok([1, 2, 3]) is None

    def test_none_returns_none(self):
        assert _result_envelope_ok(None) is None

    def test_dict_with_non_bool_ok(self):
        # Defensive — only treat 'ok' as a result-envelope flag when
        # it's actually a bool.
        assert _result_envelope_ok({"ok": "yes"}) is None
