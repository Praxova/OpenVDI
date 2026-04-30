"""MCP server logging configuration.

Text format (default) for development; JSON format for production
(per C8). Output goes to stderr — stdout is reserved for the MCP
protocol's own messages (FastMCP over stdio).

M5-08 extends this with request_id propagation; M5-02 ships only the
formatter swap.
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Any


class _JSONFormatter(logging.Formatter):
    """Simple JSON line-per-record formatter. M5-08 adds request_id."""

    # Collected once at class construction so per-record formatting is
    # cheap — getting these via a fresh LogRecord on every call would
    # work but is wasteful.
    _STANDARD_KEYS = frozenset(
        logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%f"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Include any extra= kwargs the call site passed.
        for k, v in record.__dict__.items():
            if k not in self._STANDARD_KEYS and not k.startswith("_"):
                payload[k] = v
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


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
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )

    root = logging.getLogger()
    # pytest and FastMCP both fiddle with handlers. Clearing first
    # is defensive against test pollution and re-configuration.
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
