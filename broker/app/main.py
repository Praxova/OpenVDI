"""OpenVDI broker — FastAPI app shell, lifespan, and error envelope.

What lives here (M2-11):
- The FastAPI app itself, with title/description/version.
- A lifespan handler that:
  * refuses to start without OPENVDI_AUTH_MODE=dev (M2 guard),
  * configures logging,
  * constructs one HypervisorProvider per registered cluster into
    app.state.providers, spawning a background ping task per cluster
    to flip pending → active|offline,
  * drains pings, closes providers, and disposes the DB engine on
    shutdown.
- The `get_provider` FastAPI dependency for handlers to resolve a live
  provider by cluster UUID.
- The exception-handler family that shapes EVERY error response into
  the APIResponse envelope — including the M2-10 forward fix for
  dependency-raised HTTPException.
- The dev-auth middleware registered (M2-12 will prepend audit).
- The admin/user router aggregation points, mounted at /api/v1 and
  /api/v1/me.
- The /health liveness endpoint.

What does NOT live here: business endpoints, provisioning logic,
broker logic, audit middleware, CORS, OpenAPI customization. Those
arrive in M2-12 through M2-17.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, AsyncIterator
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy import select
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from app.api.auth import auth_router
from app.api.router import admin_router, user_router
from app.config import get_settings
from app.database import async_session_factory, dispose_engine
from app.logging import configure_logging
from app.middleware.audit import AuditMiddleware
from app.middleware.auth import DevAuthMiddleware, JWTAuthMiddleware
from app.middleware.request_id import RequestIdMiddleware
from app.models import Cluster, ClusterStatus
from app.providers.base import HypervisorProvider
from app.providers.exceptions import (
    ProviderAuthError,
    ProviderCapabilityError,
    ProviderError,
    ProviderLockError,
    ProviderNotFoundError,
    ProviderTaskError,
    ProviderTimeoutError,
)
from app.services.broker import (
    BrokerError,
    NotEntitledError,
    PoolFullError,
    PoolInactiveError,
)
from app.services.cluster_service import construct_provider, ping_and_update_status
from app.services.jwt_service import JWTService
from app.services.ldap_service import LDAPService
from app.services.provisioner import PoolInactive
from app.services.session_tracker import InvalidSessionStateError
from app.services.vmid_allocator import VMIDRangeConflict, VMIDRangeExhausted
from app.workers import WORKERS, WorkerRunner


logger = logging.getLogger(__name__)


# ── Startup guards & logging ──────────────────────────────────


def _ensure_encryption_key() -> None:
    """Fail fast at startup if the Fernet key is missing or malformed.

    Without a valid key, `decrypt_secret` will raise on every cluster
    row at construction time, leaving every cluster `offline` for an
    obscure reason. Detecting it here surfaces a clear error message
    before any cluster loads — matches the M2-11 dev-auth gate.
    """
    key = os.environ.get("OPENVDI_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError(
            "OPENVDI_ENCRYPTION_KEY environment variable is required. "
            "Generate one with: python -m app.crypto generate-key"
        )
    # Validate format without holding any plaintext — Fernet's constructor
    # raises on malformed keys (wrong length, non-base64).
    try:
        from cryptography.fernet import Fernet
        Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as exc:
        raise RuntimeError(
            f"OPENVDI_ENCRYPTION_KEY is malformed: {exc}"
        )


def _configure_logging() -> None:
    """Configure logging from Settings (M4-12).

    Replaces M2's inline os.environ reads + basicConfig. The actual
    formatter / filter / handler wiring lives in app/logging.py;
    Settings is the single source of truth (per X6).
    """
    settings = get_settings()
    configure_logging(
        log_format=settings.openvdi_log_format,
        level=settings.openvdi_log_level,
        level_httpx=settings.openvdi_log_level_httpx,
    )


# ── Envelope helpers used by exception handlers ───────────────

def _is_admin(request: Request) -> bool:
    """True iff the authenticated user has role=admin.

    Tolerant of missing request.state.user — defaults to non-admin,
    which is the safe side (non-admin responses omit diagnostic details).
    """
    user = getattr(request.state, "user", None)
    return user is not None and getattr(user, "role", "user") == "admin"


def _envelope(
    code: str, message: str, details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        err["details"] = details
    return {"data": None, "error": err}


def _json_safe(value: Any) -> Any:
    """Recursively coerce a value so json.dumps accepts it.

    Pydantic's `exc.errors()` output can contain non-JSON-native
    inputs — `bytes` when a request body failed to decode, datetimes,
    UUIDs, Decimals. Rather than guessing each case, we handle the
    well-known ones explicitly and fall back to `repr()`.

    `repr()` is acceptable here because this data is surfaced only
    to admins in error details — goal is "give enough context to
    diagnose", not "round-trip the value."
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return f"<bytes len={len(value)}>"
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        # Pydantic errors sometimes use tuple-valued keys (`('body',)`
        # for top-level errors). Coerce keys to str so json.dumps accepts.
        return {str(k): _json_safe(v) for k, v in value.items()}
    return repr(value)


# ── Lifespan ──────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: construct providers + spawn pings.
    Shutdown: drain pings, close providers, dispose engine.
    """
    _ensure_encryption_key()
    _configure_logging()
    logger.info("OpenVDI broker starting")

    # Auth-mode gating. Settings's model validator already enforces the
    # required env-var set in jwt mode (M4-02); we just construct the
    # services here. In dev mode app.state.{jwt,ldap}_service stay None
    # and the dependency factories raise 503 AUTH_MODE_NOT_SUPPORTED.
    settings = get_settings()
    if settings.openvdi_auth_mode == "jwt":
        app.state.jwt_service = JWTService(settings)
        app.state.ldap_service = LDAPService(settings)
        logger.info("JWT auth services initialized")
    else:
        app.state.jwt_service = None
        app.state.ldap_service = None
        logger.warning(
            "Broker started in DEV auth mode — JWT endpoints disabled. "
            "Set OPENVDI_AUTH_MODE=jwt for production."
        )

    app.state.providers: dict[UUID, HypervisorProvider] = {}
    # Per-broker timestamp of when each provider was constructed. M4-11's
    # health_checker uses this to detect cluster-config-sync events
    # (W13): when `cluster.updated_at` advances past this timestamp, the
    # broker reconstructs its provider. Lost on restart by design — the
    # lifespan rebuilds everything fresh.
    app.state.provider_constructed_at: dict[UUID, datetime] = {}
    app.state.ping_tasks: set[asyncio.Task] = set()

    # Load clusters the broker should try to serve.
    # 'maintenance' is admin-disabled — skip. Anything outside the
    # documented states is also skipped defensively.
    eligible_states = (
        ClusterStatus.PENDING.value,
        ClusterStatus.ACTIVE.value,
        ClusterStatus.OFFLINE.value,
    )

    async with async_session_factory() as session:
        clusters = (
            await session.execute(
                select(Cluster).where(Cluster.status.in_(eligible_states))
            )
        ).scalars().all()

        to_mark_offline: list[Cluster] = []
        for cluster in clusters:
            try:
                provider = await construct_provider(cluster)
            except Exception as exc:
                # W-6-c: startup tolerates broken clusters. Log, mark
                # offline, keep going. construct_provider raises for
                # unknown provider_type, decrypt failure, or constructor
                # validation — none of which should bring the broker
                # down.
                logger.error(
                    "failed to construct provider for cluster %s: %s: %s",
                    cluster.name, type(exc).__name__, exc,
                )
                to_mark_offline.append(cluster)
                continue
            app.state.providers[cluster.id] = provider
            app.state.provider_constructed_at[cluster.id] = (
                datetime.now(timezone.utc)
            )
            logger.info(
                "constructed provider for cluster %s (%s)",
                cluster.name, cluster.id,
            )

        for cluster in to_mark_offline:
            cluster.status = ClusterStatus.OFFLINE.value
        await session.commit()

    # Spawn background pings so the lifespan returns quickly. Tasks
    # auto-remove themselves from the set on completion. The shared
    # helper opens its own session and is the canonical writer of
    # `clusters.status` — see services/cluster_service.py.
    for cid, provider in app.state.providers.items():
        task = asyncio.create_task(ping_and_update_status(cid, provider))
        app.state.ping_tasks.add(task)
        task.add_done_callback(app.state.ping_tasks.discard)

    # In-flight task discovery is the task_tracker worker's job (M4-10).
    # Its first tick (≤5s after lifespan) finds every Desktop row with
    # pve_task_upid set and drives polling, regardless of which broker
    # set the UPID. The M2 BackgroundTasks-based resume hook is gone.

    # ── Workers framework (M4-07) ────────────────────────────
    # Each worker self-elects a leader via pg_try_advisory_lock; this
    # broker may end up leading none, some, or all of the registered
    # workers depending on which leader-locks it acquires. Multi-broker
    # safe out of the box — see docs/deploy.md → Multi-Broker.
    runner = WorkerRunner(app, [cls() for cls in WORKERS])
    app.state.worker_runner = runner
    await runner.start()

    logger.info(
        "OpenVDI broker ready: %d provider(s), %d ping task(s), "
        "%d worker(s)",
        len(app.state.providers),
        len(app.state.ping_tasks),
        len(WORKERS),
    )

    try:
        yield
    finally:
        logger.info("OpenVDI broker shutting down")
        # Stop workers FIRST — they hold lock-holder connections from
        # the engine pool that need to release before dispose_engine().
        await app.state.worker_runner.stop()
        # Drain in-flight cluster ping tasks. return_exceptions=True so
        # a failed task doesn't mask a second error during cleanup.
        all_tasks = list(app.state.ping_tasks)
        if all_tasks:
            await asyncio.gather(*all_tasks, return_exceptions=True)
        for cid, provider in app.state.providers.items():
            try:
                await provider.close()
            except Exception as exc:
                logger.warning(
                    "provider close failed for cluster %s: %s", cid, exc,
                )
        await dispose_engine()
        logger.info("OpenVDI broker shutdown complete")


# ── App ───────────────────────────────────────────────────────

app = FastAPI(
    title="OpenVDI Broker",
    description="Open-source VDI management layer.",
    version="0.2.0",
    lifespan=lifespan,
)


# ── Middleware ────────────────────────────────────────────────
#
# add_middleware is LIFO — the LAST added runs OUTERMOST. Target order
# outermost → innermost:
#   RequestId (M4-12) → Auth → Audit → handlers.
# RequestId outermost so the request_id ContextVar is set before any
# other middleware logs. Auth next so it sets request.state.user.
# Audit innermost so it can record the actor.
#
# Auth-mode pick: the DevAuth path stays for local development against
# the M2 X-Dev-* header contract; production runs JWTAuth against the
# access tokens M4-04's auth endpoints issue. Switching modes requires
# a broker restart — settings is read once at module import.
app.add_middleware(AuditMiddleware)      # innermost: reads request.state.user
if get_settings().is_dev_auth:
    app.add_middleware(DevAuthMiddleware)
    logger.warning(
        "Auth middleware: DevAuth (X-Dev-* headers). Set "
        "OPENVDI_AUTH_MODE=jwt for production."
    )
else:
    app.add_middleware(JWTAuthMiddleware)
    logger.info("Auth middleware: JWT (Bearer access token)")
app.add_middleware(RequestIdMiddleware)  # outermost: sets request_id ContextVar


# ── Dependencies ──────────────────────────────────────────────

async def get_provider(
    cluster_id: UUID, request: Request,
) -> HypervisorProvider:
    """Look up a live provider by cluster id.

    404 if the cluster isn't in the providers map — either it doesn't
    exist, it was in 'maintenance' at startup and skipped, or its
    construction failed and it's now 'offline' in the DB.

    Handlers that have a path parameter named `cluster_id: UUID` can
    declare `provider: HypervisorProvider = Depends(get_provider)` and
    FastAPI binds the path param automatically. Handlers that resolve
    the cluster indirectly (via a pool, desktop, etc.) look up the
    cluster_id themselves and call `get_provider(cluster_id, request)`
    explicitly — there's no magic dependency chain for that case.
    """
    provider = request.app.state.providers.get(cluster_id)
    if provider is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": f"no active provider for cluster {cluster_id}",
            },
        )
    return provider


# ── Exception handlers ────────────────────────────────────────
#
# Every error response leaves the broker shaped as the APIResponse
# envelope. If a new exception type appears and there's no handler,
# the catch-all at the bottom produces a 500 envelope; that's
# acceptable as a safety net but prefer a specific handler.


# Provider errors: subclass dispatch, admin-gated details.
_PROVIDER_STATUS_MAP: list[tuple[type[ProviderError], int, str]] = [
    # Order matters — first match wins. Put subclasses before their
    # base class; here all entries are siblings under ProviderError.
    (ProviderAuthError, 502, "PROVIDER_ERROR"),
    (ProviderNotFoundError, 404, "NOT_FOUND"),
    (ProviderTimeoutError, 504, "PROVIDER_TIMEOUT"),
    (ProviderLockError, 503, "PROVIDER_ERROR"),
    (ProviderCapabilityError, 501, "PROVIDER_ERROR"),
    (ProviderTaskError, 502, "PROVIDER_ERROR"),
]


@app.exception_handler(ProviderError)
async def handle_provider_error(
    request: Request, exc: ProviderError,
) -> JSONResponse:
    status, code = 502, "PROVIDER_ERROR"
    for cls, s, c in _PROVIDER_STATUS_MAP:
        if isinstance(exc, cls):
            status, code = s, c
            break

    details: dict[str, Any] | None = None
    if _is_admin(request):
        details = {"provider": getattr(exc, "provider_type", "unknown")}
        raw = getattr(exc, "detail", None)
        if raw:
            details.update(raw)

    logger.warning(
        "provider error (%s): %s",
        type(exc).__name__, exc,
        extra={"status": status, "code": code},
    )
    return JSONResponse(
        status_code=status,
        content=_envelope(code, str(exc), details),
    )


# Domain errors — one short handler each.

@app.exception_handler(NotEntitledError)
async def handle_not_entitled(request: Request, exc: NotEntitledError) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content=_envelope("FORBIDDEN", str(exc)),
    )


@app.exception_handler(PoolFullError)
async def handle_pool_full(request: Request, exc: PoolFullError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content=_envelope("POOL_FULL", str(exc)),
    )


@app.exception_handler(PoolInactiveError)
async def handle_pool_inactive_broker(
    request: Request, exc: PoolInactiveError,
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content=_envelope("CONFLICT", str(exc)),
    )


@app.exception_handler(PoolInactive)
async def handle_pool_inactive_provisioner(
    request: Request, exc: PoolInactive,
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content=_envelope("CONFLICT", str(exc)),
    )


@app.exception_handler(InvalidSessionStateError)
async def handle_invalid_session_state(
    request: Request, exc: InvalidSessionStateError,
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content=_envelope("CONFLICT", str(exc)),
    )


@app.exception_handler(VMIDRangeExhausted)
async def handle_vmid_range_exhausted(
    request: Request, exc: VMIDRangeExhausted,
) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content=_envelope("POOL_FULL", str(exc)),
    )


@app.exception_handler(VMIDRangeConflict)
async def handle_vmid_range_conflict(
    request: Request, exc: VMIDRangeConflict,
) -> JSONResponse:
    """Admin-gated details with conflict-list truncation.

    The exception instance keeps the full conflicts list for audit
    downstream; the wire response caps at 20 entries with a flag.
    """
    conflicts = list(getattr(exc, "conflicts", []))
    truncated = conflicts[:20]
    details: dict[str, Any] = {
        "source": getattr(exc, "source", "unknown"),
        "conflicts": truncated,
    }
    if len(conflicts) > 20:
        details["conflicts_truncated"] = True
        details["conflicts_total"] = len(conflicts)
    return JSONResponse(
        status_code=409,
        content=_envelope(
            "CONFLICT", str(exc), details if _is_admin(request) else None,
        ),
    )


@app.exception_handler(BrokerError)
async def handle_broker_error(
    request: Request, exc: BrokerError,
) -> JSONResponse:
    # BrokerError is the base; its subclasses (NotEntitledError,
    # PoolFullError, PoolInactiveError) have their own handlers above.
    # This catches the bare BrokerError case only.
    logger.error("broker error: %s", exc)
    return JSONResponse(
        status_code=500,
        content=_envelope("INTERNAL_ERROR", str(exc)),
    )


# HTTPException override — THE M2-10 FIX.
#
# Without this, dependencies that raise HTTPException(status_code=403,
# detail={"code": "FORBIDDEN", ...}) produce {"detail": {...}} on the
# wire — not the envelope. With it installed, every raise HTTPException
# site in the codebase produces correctly-shaped responses.
#
# Register against the Starlette base; FastAPI's HTTPException is a
# subclass so both match, and bare Starlette paths are also covered.

_STATUS_TO_CODE: dict[int, str] = {
    400: "INVALID_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "INVALID_REQUEST",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(
    request: Request, exc: StarletteHTTPException,
) -> JSONResponse:
    """Reshape raised HTTPException into the APIResponse envelope.

    `detail` may be:
      - a dict with 'code' + 'message' (our convention — pass through)
      - a dict without those keys (defensive — shape from status code)
      - a string (FastAPI default for bare 404 etc.)
      - None
    """
    if (
        isinstance(exc.detail, dict)
        and "code" in exc.detail
        and "message" in exc.detail
    ):
        err = dict(exc.detail)  # shallow copy
        return JSONResponse(
            status_code=exc.status_code,
            content={"data": None, "error": err},
        )

    code = _STATUS_TO_CODE.get(exc.status_code, "ERROR")
    message = str(exc.detail) if exc.detail else f"HTTP {exc.status_code}"
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(code, message),
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(
    request: Request, exc: RequestValidationError,
) -> JSONResponse:
    """Reshape Pydantic validation errors. Full error list is admin-only
    (leaking it to all users enumerates the schema unnecessarily).

    `exc.errors()` can contain `bytes` / `datetime` / `UUID` etc. that
    `json.dumps` rejects by default — route through `_json_safe` to
    coerce to JSON-native forms before response construction.
    """
    details = (
        {"errors": _json_safe(exc.errors())} if _is_admin(request) else None
    )
    return JSONResponse(
        status_code=422,
        content=_envelope(
            "INVALID_REQUEST", "request validation failed", details,
        ),
    )


@app.exception_handler(Exception)
async def handle_unexpected(
    request: Request, exc: Exception,
) -> JSONResponse:
    """Safety net. logger.exception captures the traceback."""
    logger.exception("unhandled exception")
    details = (
        {"type": type(exc).__name__, "message": str(exc)}
        if _is_admin(request) else None
    )
    return JSONResponse(
        status_code=500,
        content=_envelope(
            "INTERNAL_ERROR", "an unexpected error occurred", details,
        ),
    )


# ── Routers ───────────────────────────────────────────────────

# Import the endpoint modules to trigger decorator-time route
# registration onto admin_router. They're unused here from a name
# perspective — the decorators are the side effect.
from app.api import (  # noqa: E402, F401  (import-for-side-effect)
    audit,
    clusters,
    dashboard,
    desktops,
    entitlements,
    pools,
    sessions,
    templates,
)
# Aliased to sidestep a name collision with local `user` variables
# elsewhere in this module (e.g. in _is_admin).
from app.api import user as _user_api  # noqa: E402, F401

app.include_router(auth_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.include_router(user_router, prefix="/api/v1/me")


# ── Health ────────────────────────────────────────────────────

@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    """Liveness probe. Intentionally plain shape, NOT the envelope —
    tooling (uvicorn, k8s probes) expects an unwrapped OK."""
    return {"status": "ok"}
