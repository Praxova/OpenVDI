"""Request-ID middleware (X-Request-ID injection + ContextVar set).

Pure-ASGI per the M2-11 posture. Reads X-Request-ID from incoming
requests (validated; rejects malformed); generates a fresh UUID4 if
absent or invalid. Sets app.logging.current_request_id_var so log
records inside the request scope are tagged. Wraps the outgoing
`send` to inject X-Request-ID into the response headers.

Per X2: every request log line carries request_id. The ContextVar
+ logging filter (in app.logging) is what ties them together; this
middleware is the producer side.
"""
from __future__ import annotations

import logging
import uuid

from app.logging import current_request_id_var

logger = logging.getLogger(__name__)


# Hard cap on client-supplied X-Request-ID values. UUID/ULID schemes
# are ≤40 chars; allow some headroom but reject obviously-malformed
# inputs so we don't put 10MB strings in every log line.
_MAX_REQUEST_ID_LENGTH = 128


class RequestIdMiddleware:
    """Pure ASGI middleware:
      - Reads X-Request-ID from the incoming request.
      - If absent / malformed: generates a fresh UUID4.
      - Sets app.logging.current_request_id_var (ContextVar).
      - Wraps `send` to inject the header on the response.

    Should be the OUTERMOST middleware — call add_middleware LAST so
    the ContextVar is set before any other middleware logs anything.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = self._extract_or_generate(scope)
        token = current_request_id_var.set(request_id)

        request_id_bytes = request_id.encode("ascii")

        async def send_with_header(message):
            if message["type"] == "http.response.start":
                # Append, don't replace — preserve any other headers
                # downstream middlewares / handlers added.
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id_bytes))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            # Restore the previous ContextVar value (None outside the
            # request, or whatever the parent context had if requests
            # nest somehow). Without reset, the ContextVar would leak
            # across request boundaries via the asyncio context.
            current_request_id_var.reset(token)

    @staticmethod
    def _extract_or_generate(scope) -> str:
        """Return a valid request id from the X-Request-ID header,
        falling back to a fresh UUID4 on absence / malformed input.

        Rules:
          - ASCII only (decode failure → ignore client value).
          - Length cap at 128 characters.
          - Whitespace stripped.
          - Empty after stripping → generate fresh.
        """
        for key, value in scope.get("headers", []):
            if key.lower() != b"x-request-id":
                continue
            if not value:
                break
            if len(value) > _MAX_REQUEST_ID_LENGTH:
                logger.debug(
                    "client-supplied X-Request-ID exceeded length cap",
                    extra={"length": len(value)},
                )
                break
            try:
                stripped = value.decode("ascii").strip()
            except UnicodeDecodeError:
                logger.debug(
                    "client-supplied X-Request-ID was not ASCII"
                )
                break
            if not stripped:
                break
            return stripped
        return str(uuid.uuid4())
