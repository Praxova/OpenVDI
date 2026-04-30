"""Logging formatter tests."""
from __future__ import annotations

import json
import logging

from openvdi_admin.logging import (
    _JSONFormatter,
    _TextFormatter,
    configure_logging,
)


def _make_record(
    *,
    level: int = logging.INFO,
    msg: str = "msg",
    name: str = "test",
    extras: dict | None = None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name, level=level, pathname="x", lineno=1,
        msg=msg, args=(), exc_info=None,
    )
    if extras:
        for k, v in extras.items():
            setattr(record, k, v)
    return record


class TestJSONFormatter:
    def test_basic_record_structure(self):
        fmt = _JSONFormatter()
        record = _make_record(msg="hello")
        data = json.loads(fmt.format(record))
        assert data["level"] == "INFO"
        assert data["message"] == "hello"
        assert data["logger"] == "test"
        assert "timestamp" in data

    def test_extra_fields_surface_in_payload(self):
        fmt = _JSONFormatter()
        record = _make_record(extras={
            "tool": "openvdi_x",
            "request_id": "abc-123",
            "duration_ms": 42,
            "outcome": "ok",
            "result_envelope_ok": True,
        })
        data = json.loads(fmt.format(record))
        assert data["tool"] == "openvdi_x"
        assert data["request_id"] == "abc-123"
        assert data["duration_ms"] == 42
        assert data["outcome"] == "ok"
        assert data["result_envelope_ok"] is True

    def test_underscore_prefixed_extras_skipped(self):
        # Internal/non-emit convention: keys starting with _ stay
        # out of the formatted payload.
        fmt = _JSONFormatter()
        record = _make_record(extras={
            "tool": "openvdi_x", "_private": "secret",
        })
        data = json.loads(fmt.format(record))
        assert data["tool"] == "openvdi_x"
        assert "_private" not in data

    def test_standard_logrecord_attrs_not_emitted(self):
        # `module`, `pathname`, `process`, etc. shouldn't bleed into
        # the JSON payload — only `extra=` kwargs do.
        fmt = _JSONFormatter()
        record = _make_record()
        data = json.loads(fmt.format(record))
        for key in ("pathname", "process", "thread", "module"):
            assert key not in data, (
                f"standard LogRecord attr {key} leaked into payload"
            )

    def test_serializes_non_string_values(self):
        # Non-string extras (int, None, dict, etc.) must serialize
        # without raising — `default=str` covers exotic types.
        fmt = _JSONFormatter()
        record = _make_record(extras={
            "count": 5,
            "result_envelope_ok": None,
            "details": {"nested": True},
        })
        data = json.loads(fmt.format(record))
        assert data["count"] == 5
        assert data["result_envelope_ok"] is None
        assert data["details"] == {"nested": True}


class TestTextFormatter:
    def test_record_without_extras_has_no_bracket(self):
        fmt = _TextFormatter()
        record = _make_record(msg="plain message")
        out = fmt.format(record)
        assert "INFO" in out
        assert "plain message" in out
        assert "[" not in out

    def test_record_with_tool_extras_appends_bracket(self):
        fmt = _TextFormatter()
        record = _make_record(
            msg="completed",
            extras={
                "tool": "openvdi_thing",
                "request_id": "abc",
                "outcome": "ok",
                "duration_ms": 50,
            },
        )
        out = fmt.format(record)
        assert "tool=openvdi_thing" in out
        assert "request_id=abc" in out
        assert "outcome=ok" in out
        assert "duration_ms=50" in out

    def test_only_known_fields_lifted(self):
        # Other extras (e.g. exception_code) don't appear in the
        # text bracket — the JSON formatter is what operators grep
        # for full structure.
        fmt = _TextFormatter()
        record = _make_record(
            extras={
                "tool": "openvdi_x",
                "exception_code": "CONFLICT",
            },
        )
        out = fmt.format(record)
        assert "tool=openvdi_x" in out
        assert "exception_code" not in out


class TestConfigureLogging:
    def test_json_format_outputs_valid_json(self, capsys):
        configure_logging(format="json", level="INFO")
        log = logging.getLogger("test_json_format")
        log.info("structured", extra={"tool": "openvdi_x"})
        captured = capsys.readouterr()
        line = captured.err.strip().split("\n")[-1]
        data = json.loads(line)
        assert data["tool"] == "openvdi_x"
        assert data["message"] == "structured"

    def test_text_format_human_readable(self, capsys):
        configure_logging(format="text", level="INFO")
        log = logging.getLogger("test_text_format")
        log.info("hi there", extra={"tool": "openvdi_y"})
        captured = capsys.readouterr()
        assert "hi there" in captured.err
        assert "tool=openvdi_y" in captured.err

    def test_clears_existing_handlers(self, capsys):
        # Defensive against pytest/FastMCP handler pollution.
        configure_logging(format="json", level="INFO")
        before = len(logging.getLogger().handlers)
        configure_logging(format="json", level="INFO")
        after = len(logging.getLogger().handlers)
        # Should be 1 either way — repeated calls don't accumulate
        # handlers.
        assert before == 1
        assert after == 1
