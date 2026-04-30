"""MCP server logging configuration.

Text format (default) for development; JSON format for production
(per C8). Output goes to stderr — stdout is reserved for the MCP
protocol's own messages (FastMCP over stdio).

Both formatters pick up `extra=` kwargs passed to a logger call —
tool wrappers, intent tools, and BrokerClient/Auth modules attach
fields like `tool`, `request_id`, `outcome`, `duration_ms` and
they appear alongside the message in the structured output.
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Any


# Snapshot of the standard LogRecord attribute set at module import.
# Anything in record.__dict__ that ISN'T in this set is from an
# `extra=...` kwarg and gets surfaced in the formatted output.
# Captured via a sentinel record so `taskName` (3.12+), `message`
# (post-call computed), etc. are all included.
_STANDARD_LOGRECORD_KEYS = frozenset(
    logging.LogRecord(
        "", 0, "", 0, "", None, None,
    ).__dict__.keys()
) | {"message", "asctime", "taskName"}


class _JSONFormatter(logging.Formatter):
    """JSON line-per-record. Standard fields plus any `extra=` kwargs.

    Underscore-prefixed extras are skipped — the convention for
    "internal use; don't emit." For instance a wrapper that wants to
    pass context to another wrapper without exposing it in the log.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(
                record, "%Y-%m-%dT%H:%M:%S.%f",
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k in _STANDARD_LOGRECORD_KEYS:
                continue
            if k.startswith("_"):
                continue
            payload[k] = v
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


# Fields the text formatter always lifts into the bracket suffix
# when present. Order matters: tool first, then request_id, then
# outcome+duration. Other extras stay invisible in text mode (the
# JSON formatter is what operators grep).
_TEXT_BRACKET_KEYS: tuple[str, ...] = (
    "tool", "request_id", "outcome", "duration_ms",
)


class _TextFormatter(logging.Formatter):
    """Human-readable formatter. Includes tool / request_id /
    outcome / duration_ms in a bracket suffix when present."""

    def format(self, record: logging.LogRecord) -> str:
        base = (
            f"{self.formatTime(record, '%H:%M:%S')} "
            f"{record.levelname:<7} "
            f"{record.name}: {record.getMessage()}"
        )
        extras = []
        for key in _TEXT_BRACKET_KEYS:
            if key in record.__dict__:
                extras.append(f"{key}={record.__dict__[key]}")
        if extras:
            base = f"{base} [{' '.join(extras)}]"
        if record.exc_info:
            base = f"{base}\n{self.formatException(record.exc_info)}"
        return base


def configure_logging(*, format: str, level: str) -> None:
    """Configure the root logger. Called once at server startup.

    Logs go to stderr — stdout is reserved for the MCP protocol's
    own messages over stdio. Anything written to stdout other than
    valid MCP frames will break the parent agent's parser.
    """
    handler = logging.StreamHandler(stream=sys.stderr)
    if format == "json":
        handler.setFormatter(_JSONFormatter())
    else:
        handler.setFormatter(_TextFormatter())

    root = logging.getLogger()
    # pytest and FastMCP both fiddle with handlers. Clearing first
    # is defensive against test pollution and re-configuration.
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
