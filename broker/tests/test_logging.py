"""Tests for app.logging — JSONFormatter, RequestIdFilter,
configure_logging."""
from __future__ import annotations

import json
import logging
import sys
from uuid import uuid4

import pytest

from app.logging import (
    JSONFormatter,
    RequestIdFilter,
    configure_logging,
    current_request_id_var,
)


@pytest.fixture
def restore_root_handlers():
    """Save/restore root logger state. configure_logging() clears root
    handlers, including pytest's caplog handler — without this fixture,
    later tests using caplog would see no records.
    """
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    try:
        yield
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)


# ── Helpers ─────────────────────────────────────────────────


def _make_record(
    *,
    name: str = "test",
    level: int = logging.INFO,
    msg: str = "hello %s",
    args: tuple = ("world",),
    exc_info=None,
    **extras,
) -> logging.LogRecord:
    """Build a LogRecord with optional extras (assigned via setattr
    after construction — same shape as logger.info(msg, extra={...})
    produces).
    """
    record = logging.LogRecord(
        name=name, level=level, pathname=__file__, lineno=10,
        msg=msg, args=args, exc_info=exc_info,
    )
    for k, v in extras.items():
        setattr(record, k, v)
    return record


# ── JSONFormatter tests ─────────────────────────────────────


def test_json_formatter_produces_valid_json():
    record = _make_record(msg="test message", args=())
    record.request_id = "-"
    out = JSONFormatter().format(record)
    parsed = json.loads(out)
    assert parsed["message"] == "test message"
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test"
    assert "timestamp" in parsed


def test_json_formatter_includes_caller_extras():
    record = _make_record(worker="echo", desktop_id="abc-123")
    record.request_id = "-"
    out = JSONFormatter().format(record)
    parsed = json.loads(out)
    assert parsed["worker"] == "echo"
    assert parsed["desktop_id"] == "abc-123"


def test_json_formatter_omits_request_id_when_dash():
    record = _make_record()
    record.request_id = "-"
    out = JSONFormatter().format(record)
    parsed = json.loads(out)
    assert "request_id" not in parsed


def test_json_formatter_includes_request_id_when_set():
    record = _make_record()
    record.request_id = "abc-123"
    out = JSONFormatter().format(record)
    parsed = json.loads(out)
    assert parsed["request_id"] == "abc-123"


def test_json_formatter_handles_uuid_extras():
    """UUIDs in extras should serialize via default=str."""
    rid = uuid4()
    record = _make_record(desktop_id=rid)
    record.request_id = "-"
    out = JSONFormatter().format(record)
    parsed = json.loads(out)
    assert parsed["desktop_id"] == str(rid)


def test_json_formatter_includes_exception():
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = _make_record(exc_info=sys.exc_info())
        record.request_id = "-"
        out = JSONFormatter().format(record)
    parsed = json.loads(out)
    assert "exception" in parsed
    assert "RuntimeError: boom" in parsed["exception"]


def test_json_formatter_excludes_standard_attrs():
    """Standard LogRecord fields like `pathname`, `lineno` don't leak
    into the JSON output."""
    record = _make_record()
    record.request_id = "-"
    out = JSONFormatter().format(record)
    parsed = json.loads(out)
    assert "pathname" not in parsed
    assert "lineno" not in parsed
    assert "thread" not in parsed


# ── RequestIdFilter tests ───────────────────────────────────


def test_filter_sets_dash_when_contextvar_is_default():
    record = _make_record()
    RequestIdFilter().filter(record)
    assert record.request_id == "-"


def test_filter_reads_contextvar_value():
    token = current_request_id_var.set("abc-xyz")
    try:
        record = _make_record()
        RequestIdFilter().filter(record)
        assert record.request_id == "abc-xyz"
    finally:
        current_request_id_var.reset(token)


# ── configure_logging tests ─────────────────────────────────


def test_configure_logging_json_emits_json(capsys, restore_root_handlers):
    """End-to-end smoke: configure with format=json, log a line, parse
    captured stderr."""
    configure_logging(
        log_format="json", level="INFO", level_httpx="WARNING",
    )
    logging.getLogger("smoke").info(
        "test message", extra={"worker": "echo"},
    )
    # The handler is a StreamHandler() with default (stderr); pytest
    # capsys captures stderr.
    captured = capsys.readouterr()
    line = (captured.err or captured.out).strip().splitlines()[-1]
    parsed = json.loads(line)
    assert parsed["message"] == "test message"
    assert parsed["worker"] == "echo"


def test_configure_logging_text_emits_human_readable(
    capsys, restore_root_handlers,
):
    configure_logging(
        log_format="text", level="INFO", level_httpx="WARNING",
    )
    logging.getLogger("smoke").info("test message")
    captured = capsys.readouterr()
    line = (captured.err or captured.out).strip().splitlines()[-1]
    assert "INFO" in line
    assert "test message" in line
    # request_id placeholder when no request in scope.
    assert "[-]" in line


def test_configure_logging_replaces_handlers(
    capsys, restore_root_handlers,
):
    """Calling configure_logging twice replaces handlers; the second
    config is what produces output (no double-emission)."""
    configure_logging(
        log_format="json", level="INFO", level_httpx="WARNING",
    )
    configure_logging(
        log_format="text", level="INFO", level_httpx="WARNING",
    )
    logging.getLogger("smoke").info("once")
    captured = capsys.readouterr()
    output = (captured.err or captured.out).strip()
    # Exactly one line — not two, not zero.
    lines = output.splitlines()
    matching = [ln for ln in lines if "once" in ln]
    assert len(matching) == 1
    # And it's the text format (the second config), not JSON.
    assert "[-]" in matching[0]
