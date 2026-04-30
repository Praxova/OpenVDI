"""Structured logging for OpenVDI.

Two formatters:
  - text: human-readable (M2 default), augmented with [request_id]
    when a request is in scope.
  - json: one JSON object per log record, suitable for log-aggregator
    ingestion (Loki / Splunk / Datadog / ELK). Default in production.

A `RequestIdFilter` injects `record.request_id` from a ContextVar so
both formatters can render it uniformly. The ContextVar is set by
the M4-12 RequestIdMiddleware on each incoming HTTP request.

Configured by `configure_logging()`, called from main.py's lifespan
based on `Settings.openvdi_log_format`. M2's `_configure_logging`
direct os.environ reads are removed in this prompt — Settings is the
single source of truth (per X6).
"""
from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any


# Per-request correlation id. Set by RequestIdMiddleware (M4-12);
# read by RequestIdFilter on every log record. Default None — workers
# and other contexts outside an HTTP request see no request_id.
current_request_id_var: ContextVar[str | None] = ContextVar(
    "current_request_id", default=None,
)


class RequestIdFilter(logging.Filter):
    """Inject `record.request_id` from the ContextVar onto every log
    record. Both formatters read this attribute uniformly.

    Sets to "-" when no request is in scope so the text formatter's
    `%(request_id)s` placeholder always has a value (avoids KeyError).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id_var.get() or "-"
        return True


# Standard LogRecord attributes — exclude from JSON output's "extra"
# pull. Anything NOT in this set + NOT starting with underscore + NOT
# a rendered attribute is treated as a caller-supplied extra and
# surfaced in the JSON output.
_STANDARD_LOG_ATTRS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName",
    "taskName",  # 3.12+
})

# Attributes injected by our filters that get controlled rendering.
# Do NOT also dump them into the generic "extra" pull.
_RENDERED_ATTRS = frozenset({"request_id"})


class JSONFormatter(logging.Formatter):
    """One JSON object per log record. Shape per X1:

      {"timestamp", "level", "logger", "message",
       "request_id"?, "exception"?, "stack_info"?,
       **caller_extras}

    `caller_extras` is whatever the call site passed via
    `extra={"foo": "bar"}` — e.g. `worker`, `desktop_id`, `vmid`.

    `default=str` in `json.dumps` falls back to repr-style strings
    for non-JSON-serializable values (UUID, datetime, Path, etc.).
    Operator-friendly default; structured-logger consumers parse the
    strings as needed.
    """

    def format(self, record: logging.LogRecord) -> str:
        log: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc,
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = getattr(record, "request_id", None)
        if request_id and request_id != "-":
            log["request_id"] = request_id

        # Surface caller-supplied extras. Anything attached to the
        # record that isn't a standard LogRecord attribute or one of
        # ours is fair game.
        for key, value in record.__dict__.items():
            if (
                key in _STANDARD_LOG_ATTRS
                or key in _RENDERED_ATTRS
                or key.startswith("_")
            ):
                continue
            log[key] = value

        if record.exc_info:
            log["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            log["stack_info"] = self.formatStack(record.stack_info)

        return json.dumps(log, default=str)


def configure_logging(
    *, log_format: str, level: str, level_httpx: str,
) -> None:
    """Configure the root logger for OpenVDI.

    Replaces M2's `logging.basicConfig` setup. Called once at lifespan
    startup from main.py.

    Parameters mirror Settings fields:
      - log_format: "text" or "json"
      - level: standard logging level name ("INFO", "DEBUG", etc.)
      - level_httpx: dial httpx down independently (it's chatty on DEBUG)
    """
    if log_format == "json":
        formatter: logging.Formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            fmt=(
                "%(asctime)s %(levelname)-7s [%(request_id)s] "
                "%(name)s: %(message)s"
            ),
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.setLevel(level.upper())
    # Drop any handlers M2's basicConfig added — we replace, not stack.
    root.handlers.clear()
    root.addHandler(handler)

    # httpx is chatty on DEBUG; dial down per the existing convention
    # (M2 read this from os.environ; M4-02 hoisted it to Settings;
    # M4-12 finally consumes from Settings).
    logging.getLogger("httpx").setLevel(level_httpx.upper())
