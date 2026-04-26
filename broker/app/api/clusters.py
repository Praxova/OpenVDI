"""Cluster CRUD endpoints (M2-14 + M2-18).

Registers via decorator on `admin_router`; importing this module from
main.py is what wires the routes up.

Scope:
  - GET    /clusters         list, paginated
  - POST   /clusters         register + one-shot ping
  - GET    /clusters/{id}    read + live nodes (degrades on provider failure)
  - PUT    /clusters/{id}    update; credential rotation handled in-place
  - DELETE /clusters/{id}    cascade-checked teardown

`POST /clusters` does NOT add the new provider to `app.state.providers` —
the lifespan is the single construction site for startup, and `PUT` is
the single rotation site. A POST'd cluster gets its provider on the
next broker restart.

`PUT /clusters/{id}` flow (M2-18):
  - Non-credential fields only → fast path: setattr, commit, return.
  - Credential fields submitted but unchanged → fast path. Decrypt and
    compare so re-submitting current values doesn't churn providers.
  - Credential fields actually changed → slow path: persist with
    `status=pending`, commit, construct new provider, ping via shared
    helper, swap into `app.state.providers` on success or 502 on
    ping failure (the new creds are saved either way — see m2-18
    prompt for the rationale).

`DELETE /clusters/{id}` (M2-18):
  - Cascade guard: any pool or template referencing the cluster → 409.
  - Pop provider from `app.state.providers` BEFORE deleting the row so
    a concurrent request can't briefly resolve it. Close after commit
    so the network teardown doesn't block the transaction.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.router import admin_router
from app.crypto import decrypt_secret, encrypt_secret
from app.database import get_db_session
from app.models import Cluster, ClusterStatus, Pool, Template
from app.providers import get_provider_class
from app.providers.base import HypervisorProvider
from app.providers.exceptions import ProviderError
from app.schemas import (
    APIResponse,
    ClusterCreate,
    ClusterRead,
    ClusterReadWithNodes,
    ClusterUpdate,
    NodeInfoRead,
    PaginationParams,
)
from app.services.audit_service import log_business_event
from app.services.cluster_service import (
    construct_provider,
    ping_and_update_status,
)


logger = logging.getLogger(__name__)


# Safe sort keys. getattr(Cluster, <str>) without this allow-list would
# be a SQL-injection vector.
_CLUSTER_SORTABLE = frozenset({"name", "created_at", "updated_at", "status"})

# Credential fields. Submitting any of these triggers the
# decrypt-and-compare check in PUT — the rotation slow path runs only
# if at least one is genuinely different from the persisted value.
_CRED_FIELDS = frozenset({"api_url", "token_id", "token_secret", "verify_ssl"})


async def _safely_close(provider: HypervisorProvider) -> None:
    """Close a provider; log and swallow any failure.

    `provider.close()` is best-effort cleanup (HTTP pool drain, TCP
    FIN). Retrying isn't useful here — a failed close on the OLD
    provider during rotation must NOT fail the PUT response, since
    the new provider is already in place and serving traffic.
    """
    try:
        await provider.close()
    except Exception:
        logger.exception("provider close failed; resources may leak")


@admin_router.get(
    "/clusters", response_model=APIResponse[list[ClusterRead]],
)
async def list_clusters(
    pagination: PaginationParams = Depends(),
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[list[ClusterRead]]:
    sort_key = pagination.sort or "name"
    if sort_key not in _CLUSTER_SORTABLE:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": f"sort must be one of {sorted(_CLUSTER_SORTABLE)}",
            },
        )
    col = getattr(Cluster, sort_key)
    stmt = (
        select(Cluster)
        .order_by(col.asc() if pagination.order == "asc" else col.desc())
        .limit(pagination.limit)
        .offset(pagination.offset)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return APIResponse(
        data=[ClusterRead.model_validate(r) for r in rows],
    )


@admin_router.post(
    "/clusters",
    status_code=201,
    response_model=APIResponse[ClusterRead],
)
async def create_cluster(
    body: ClusterCreate,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[ClusterRead]:
    existing = (
        await session.execute(
            select(Cluster).where(Cluster.name == body.name)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONFLICT",
                "message": f"cluster '{body.name}' already exists",
            },
        )

    try:
        provider_cls = get_provider_class(body.provider_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": f"unknown provider_type '{body.provider_type}'",
            },
        )

    plaintext = body.token_secret.get_secret_value()
    cluster = Cluster(
        name=body.name,
        provider_type=body.provider_type,
        api_url=body.api_url,
        token_id=body.token_id,
        token_secret=encrypt_secret(plaintext),
        verify_ssl=body.verify_ssl,
        node_filter=body.node_filter,
        provider_config=body.provider_config or {},
        status=ClusterStatus.PENDING.value,
    )
    session.add(cluster)
    await session.commit()
    await session.refresh(cluster)

    # One-shot ping — ad-hoc provider, never stashed. See module docstring.
    provider = provider_cls(
        api_url=body.api_url,
        token_id=body.token_id,
        token_secret=plaintext,
        verify_ssl=body.verify_ssl,
    )
    try:
        try:
            ok = await provider.ping()
        except Exception as exc:
            logger.warning(
                "cluster ping failed on registration: %s: %s",
                type(exc).__name__, exc,
            )
            cluster.status = ClusterStatus.OFFLINE.value
        else:
            cluster.status = (
                ClusterStatus.ACTIVE.value if ok else ClusterStatus.OFFLINE.value
            )
    finally:
        try:
            await provider.close()
        except Exception:
            logger.exception(
                "provider close after cluster registration failed",
            )

    await session.commit()
    await session.refresh(cluster)
    return APIResponse(data=ClusterRead.model_validate(cluster))


@admin_router.get(
    "/clusters/{cluster_id}",
    response_model=APIResponse[ClusterReadWithNodes],
)
async def get_cluster(
    cluster_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[ClusterReadWithNodes]:
    cluster = await session.get(Cluster, cluster_id)
    if cluster is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "cluster not found"},
        )

    nodes: list[NodeInfoRead] = []
    provider = request.app.state.providers.get(cluster_id)
    if provider is not None:
        try:
            node_infos = await provider.list_nodes()
        except ProviderError as exc:
            # Deliberate degradation — return the row with empty nodes
            # so admins can still see the cluster when the hypervisor
            # is unreachable. See scope guardrails in m2-14 prompt.
            logger.warning(
                "list_nodes failed for cluster %s: %s: %s",
                cluster.name, type(exc).__name__, exc,
            )
        else:
            nodes = [NodeInfoRead.model_validate(n) for n in node_infos]

    base: dict[str, Any] = ClusterRead.model_validate(cluster).model_dump()
    return APIResponse(data=ClusterReadWithNodes(**base, nodes=nodes))


@admin_router.put(
    "/clusters/{cluster_id}",
    response_model=APIResponse[ClusterRead],
)
async def update_cluster(
    cluster_id: UUID,
    body: ClusterUpdate,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[ClusterRead]:
    cluster = await session.get(Cluster, cluster_id)
    if cluster is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "cluster not found"},
        )

    update_data = body.model_dump(exclude_unset=True)
    submitted_cred = {
        k: v for k, v in update_data.items() if k in _CRED_FIELDS
    }
    submitted_other = {
        k: v for k, v in update_data.items() if k not in _CRED_FIELDS
    }

    # ── Decrypt-and-compare ─────────────────────────────────────
    # Only matters if the caller submitted credential fields. Without
    # this short-circuit, an admin UI that echoes back current values
    # on every PUT would force a provider teardown on every save.
    cred_changed = False
    if submitted_cred:
        current_plaintext = decrypt_secret(cluster.token_secret)
        for field, new_value in submitted_cred.items():
            if field == "token_secret":
                new_secret = (
                    new_value.get_secret_value()
                    if hasattr(new_value, "get_secret_value")
                    else new_value
                )
                if new_secret != current_plaintext:
                    cred_changed = True
            elif getattr(cluster, field) != new_value:
                cred_changed = True

    # Apply non-credential changes immediately — they don't affect the
    # provider construction.
    for field, value in submitted_other.items():
        setattr(cluster, field, value)

    if not cred_changed:
        # Fast path. No provider churn even if the body included
        # credential fields, as long as the values match what's stored.
        await session.commit()
        await session.refresh(cluster)
        return APIResponse(data=ClusterRead.model_validate(cluster))

    # ── Slow path: credential rotation ─────────────────────────
    # Apply credential changes onto the row, then flip status to
    # 'pending' and commit BEFORE the network ping so we don't hold
    # row locks across a several-second I/O wait.
    for field, new_value in submitted_cred.items():
        if field == "token_secret":
            plaintext = (
                new_value.get_secret_value()
                if hasattr(new_value, "get_secret_value")
                else new_value
            )
            cluster.token_secret = encrypt_secret(plaintext)
        else:
            setattr(cluster, field, new_value)
    cluster.status = ClusterStatus.PENDING.value
    await session.commit()
    await session.refresh(cluster)

    # Construct a new provider from the freshly-committed row. If
    # construction itself fails (malformed url, bad config), the new
    # creds stay persisted — same rationale as a failed ping below.
    try:
        new_provider = await construct_provider(cluster)
    except Exception as exc:
        logger.error(
            "failed to construct new provider for cluster %s: %s: %s",
            cluster.name, type(exc).__name__, exc,
        )
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": (
                    f"could not construct provider with new credentials: {exc}"
                ),
            },
        )

    # Ping synchronously via the shared helper. Admins want immediate
    # confirmation that new credentials work. The helper opens its own
    # session, settles status, and writes a `cluster.ping.flipped`
    # audit row on transition.
    new_status = await ping_and_update_status(cluster.id, new_provider)

    if new_status != ClusterStatus.ACTIVE:
        # New creds didn't work. Close the new provider and leave the
        # old one in `app.state.providers` so the cluster is still
        # readable while the operator diagnoses. The new (broken) creds
        # ARE persisted — the operator likely typed them correctly and
        # Proxmox is just unreachable; reverting would lose their work.
        # See m2-18 acceptance for the deliberate-design flag.
        await _safely_close(new_provider)
        await session.refresh(cluster)
        raise HTTPException(
            status_code=502,
            detail={
                "code": "PROVIDER_ERROR",
                "message": (
                    f"cluster updated to status '{new_status.value}' — "
                    "the new credentials were saved but the provider could "
                    "not be reached. Verify the Proxmox API is up and the "
                    "token has required permissions."
                ),
            },
        )

    # Ping succeeded — swap providers in `app.state.providers`. The
    # dict assignment is GIL-atomic; concurrent rotations serialize at
    # the row-level lock during commit, but each call's locally-scoped
    # `new_provider` is closed safely either way (see _safely_close).
    old_provider = request.app.state.providers.get(cluster.id)
    request.app.state.providers[cluster.id] = new_provider
    if old_provider is not None:
        await _safely_close(old_provider)

    await log_business_event(
        session=session,
        actor=request.state.user.username,
        action="cluster.credentials.rotated",
        resource_type="cluster",
        resource_id=cluster.id,
        details={
            "cluster_name": cluster.name,
            "fields_changed": sorted(submitted_cred.keys()),
        },
        client_ip=request.client.host if request.client else None,
    )
    await session.commit()
    await session.refresh(cluster)
    return APIResponse(data=ClusterRead.model_validate(cluster))


@admin_router.delete("/clusters/{cluster_id}", status_code=204)
async def delete_cluster(
    cluster_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Synchronous delete with a strict cascade guard.

    Pools and templates that reference the cluster must be torn down
    first — the operator follows pools (M2-15 async cascade) → templates
    (M2-14 sync) → cluster (this endpoint). The alternative (cascade
    delete from this endpoint) was rejected: multi-minute worst case,
    failures hard to surface mid-cascade. Strict guard makes the
    sequence explicit.
    """
    cluster = await session.get(Cluster, cluster_id)
    if cluster is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "cluster not found"},
        )

    pool_count = int(
        await session.scalar(
            select(func.count(Pool.id)).where(Pool.cluster_id == cluster_id)
        ) or 0
    )
    template_count = int(
        await session.scalar(
            select(func.count(Template.id)).where(
                Template.cluster_id == cluster_id
            )
        ) or 0
    )

    if pool_count > 0:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONFLICT",
                "message": (
                    f"cluster has {pool_count} pool(s); delete or reassign "
                    "them first"
                ),
            },
        )
    if template_count > 0:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONFLICT",
                "message": (
                    f"cluster has {template_count} template(s); delete them "
                    "first"
                ),
            },
        )

    # Pop BEFORE the row delete so a concurrent request can't briefly
    # resolve a provider for a cluster that's about to be gone. The
    # `get_provider` dependency 404s cleanly when the entry is missing.
    old_provider = request.app.state.providers.pop(cluster_id, None)

    await log_business_event(
        session=session,
        actor=request.state.user.username,
        action="cluster.delete",
        resource_type="cluster",
        resource_id=cluster_id,
        details={
            "cluster_name": cluster.name,
            "provider_type": cluster.provider_type,
        },
        client_ip=request.client.host if request.client else None,
    )

    await session.delete(cluster)
    await session.commit()

    # Close after commit — provider.close() is a network teardown and
    # has no business inside the DB transaction. By now the provider is
    # unreachable from any new request (popped from the dict above), so
    # closing at our leisure is safe.
    if old_provider is not None:
        await _safely_close(old_provider)
