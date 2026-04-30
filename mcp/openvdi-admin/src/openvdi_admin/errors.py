"""BrokerError + envelope unwrapping helpers.

The broker returns {data, error} envelopes on every JSON response.
The MCP unwraps these into plain typed values (success) or a typed
exception (failure). The exception type carries the structured
fields the agent needs for branching: code, message, details,
http_status.
"""
from __future__ import annotations

from typing import Any


class BrokerError(Exception):
    """Raised when the broker responds with an error envelope, an
    unexpected status code, or a transport-level failure.

    Attributes:
        http_status: HTTP status code from the broker, or 0 for
            transport failures (DNS, connection refused, timeout).
        code: Error code from the envelope's error.code, or
            'INTERNAL_ERROR' / 'TRANSPORT_ERROR' for synthetic cases.
        message: Human-readable summary.
        details: Optional structured data from envelope's error.details.
            Often present on PROVIDER_ERROR responses for admins
            (per docs/api-design.md Error Response Shape).
    """

    def __init__(
        self,
        http_status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.code = code
        self.message = message
        self.details = details

    def __str__(self) -> str:
        return f"[{self.code}] {self.message} (HTTP {self.http_status})"

    def __repr__(self) -> str:
        return (
            f"BrokerError(http_status={self.http_status!r}, "
            f"code={self.code!r}, message={self.message!r}, "
            f"details={self.details!r})"
        )

    @classmethod
    def transport(cls, message: str) -> "BrokerError":
        """Convenience for transport-layer failures (DNS, timeout,
        connection refused). Pinned `http_status=0` so callers can
        pattern-match `err.http_status == 0` for "is this a network
        problem?"."""
        return cls(http_status=0, code="TRANSPORT_ERROR", message=message)

    @classmethod
    def envelope_missing(cls, http_status: int) -> "BrokerError":
        """The broker returned a response that didn't have the standard
        `{data, error}` shape. Treated as INTERNAL_ERROR — typically a
        proxy or upstream returning HTML on 5xx."""
        return cls(
            http_status=http_status,
            code="INTERNAL_ERROR",
            message=(
                f"broker returned non-envelope response (HTTP {http_status})"
            ),
        )


def unwrap_envelope(payload: Any, http_status: int) -> Any:
    """Given a JSON-decoded broker response, return the unwrapped data
    or raise BrokerError. Caller handles network failures upstream.

    Accepts:
      - {data: X, error: null}     → returns X
      - {data: null, error: {...}} → raises BrokerError from envelope
      - non-envelope shapes        → raises BrokerError(INTERNAL_ERROR)
    """
    if (
        not isinstance(payload, dict)
        or "data" not in payload
        or "error" not in payload
    ):
        raise BrokerError.envelope_missing(http_status)

    error = payload["error"]
    if error is not None:
        if not isinstance(error, dict):
            raise BrokerError.envelope_missing(http_status)
        raise BrokerError(
            http_status=http_status,
            code=str(error.get("code", "INTERNAL_ERROR")),
            message=str(error.get("message", "broker returned an error")),
            details=(
                error.get("details")
                if isinstance(error.get("details"), dict)
                else None
            ),
        )

    return payload["data"]
