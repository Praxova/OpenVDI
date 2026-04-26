"""Template CRUD + provider-validation endpoints (M2-14 scope).

Template registration is "light validation" here — we verify the
referenced VM exists and is a template. Richer checks (guest agent
configured, etc.) live in POST /templates/{id}/validate.

DELETE rejects if any Pool still references the template (FK guard at
the app level; the DB schema's FK is RESTRICT and would raise
IntegrityError, but a 409 with a readable message is friendlier).
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.router import admin_router
from app.database import get_db_session
from app.models import Cluster, Pool, Template
from app.providers.base import VMRef
from app.providers.exceptions import ProviderNotFoundError
from app.schemas import (
    APIResponse,
    PaginationParams,
    TemplateCreate,
    TemplateRead,
    TemplateUpdate,
    TemplateValidationResult,
    ValidationCheck,
)


logger = logging.getLogger(__name__)


_TEMPLATE_SORTABLE = frozenset(
    {"name", "created_at", "updated_at", "status", "pve_vmid"}
)


def _provider_for(
    request: Request, cluster_id: UUID, cluster_name: str, cluster_status: str,
):
    """Resolve the live provider for a cluster or 400 with context.

    Template operations need a reachable hypervisor (to validate the
    referenced VM exists). If the cluster has no active provider,
    surface that as an actionable 400 rather than failing later with a
    generic 502.
    """
    provider = request.app.state.providers.get(cluster_id)
    if provider is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": (
                    f"cluster '{cluster_name}' has no active provider "
                    f"(status={cluster_status}); cannot validate template"
                ),
            },
        )
    return provider


@admin_router.get(
    "/templates", response_model=APIResponse[list[TemplateRead]],
)
async def list_templates(
    cluster_id: UUID | None = Query(None),
    os_type: str | None = Query(None),
    status: str | None = Query(None),
    pagination: PaginationParams = Depends(),
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[list[TemplateRead]]:
    sort_key = pagination.sort or "name"
    if sort_key not in _TEMPLATE_SORTABLE:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": f"sort must be one of {sorted(_TEMPLATE_SORTABLE)}",
            },
        )
    col = getattr(Template, sort_key)
    stmt = select(Template)
    if cluster_id is not None:
        stmt = stmt.where(Template.cluster_id == cluster_id)
    if os_type:
        stmt = stmt.where(Template.os_type == os_type)
    if status:
        stmt = stmt.where(Template.status == status)
    stmt = (
        stmt
        .order_by(col.asc() if pagination.order == "asc" else col.desc())
        .limit(pagination.limit)
        .offset(pagination.offset)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return APIResponse(
        data=[TemplateRead.model_validate(r) for r in rows],
    )


@admin_router.post(
    "/templates",
    status_code=201,
    response_model=APIResponse[TemplateRead],
)
async def create_template(
    body: TemplateCreate,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[TemplateRead]:
    cluster = await session.get(Cluster, body.cluster_id)
    if cluster is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": f"cluster {body.cluster_id} not found",
            },
        )

    provider = _provider_for(request, body.cluster_id, cluster.name, cluster.status)

    ref = VMRef(
        provider_type=cluster.provider_type,
        data={"node": body.pve_node, "vmid": body.pve_vmid},
    )
    try:
        vm_status = await provider.get_vm_status(ref)
    except ProviderNotFoundError:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": (
                    f"VM {body.pve_vmid} not found on node {body.pve_node}"
                ),
            },
        )
    # Other ProviderError subclasses bubble up to M2-11's handler.

    if not vm_status.is_template:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_REQUEST",
                "message": (
                    f"VM {body.pve_vmid} on node {body.pve_node} is not a "
                    f"template. Run `qm template {body.pve_vmid}` on the "
                    "Proxmox node first."
                ),
            },
        )

    # (cluster_id, pve_vmid) has a DB UNIQUE — translate to 409 rather
    # than letting the IntegrityError surface as a 500.
    existing = (
        await session.execute(
            select(Template).where(
                Template.cluster_id == body.cluster_id,
                Template.pve_vmid == body.pve_vmid,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONFLICT",
                "message": (
                    f"template for VMID {body.pve_vmid} in cluster "
                    f"'{cluster.name}' already exists"
                ),
            },
        )

    template = Template(
        cluster_id=body.cluster_id,
        name=body.name,
        pve_vmid=body.pve_vmid,
        pve_node=body.pve_node,
        os_type=body.os_type,
        description=body.description,
        cpu_cores=body.cpu_cores,
        memory_mb=body.memory_mb,
        disk_gb=body.disk_gb,
        gpu_required=body.gpu_required,
        tags=body.tags or [],
        provider_config=body.provider_config or {},
        status="active",
    )
    session.add(template)
    await session.commit()
    await session.refresh(template)
    return APIResponse(data=TemplateRead.model_validate(template))


@admin_router.get(
    "/templates/{template_id}",
    response_model=APIResponse[TemplateRead],
)
async def get_template(
    template_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[TemplateRead]:
    template = await session.get(Template, template_id)
    if template is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "template not found"},
        )
    return APIResponse(data=TemplateRead.model_validate(template))


@admin_router.put(
    "/templates/{template_id}",
    response_model=APIResponse[TemplateRead],
)
async def update_template(
    template_id: UUID,
    body: TemplateUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[TemplateRead]:
    template = await session.get(Template, template_id)
    if template is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "template not found"},
        )
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(template, field, value)
    await session.commit()
    await session.refresh(template)
    return APIResponse(data=TemplateRead.model_validate(template))


@admin_router.post(
    "/templates/{template_id}/validate",
    response_model=APIResponse[TemplateValidationResult],
)
async def validate_template(
    template_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> APIResponse[TemplateValidationResult]:
    template = await session.get(Template, template_id)
    if template is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "template not found"},
        )

    cluster = await session.get(Cluster, template.cluster_id)
    if cluster is None:
        # Shouldn't happen under normal ops (FK is RESTRICT), but be
        # defensive — a dangling template is better surfaced than swallowed.
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NOT_FOUND",
                "message": (
                    f"template references cluster {template.cluster_id} "
                    "which no longer exists"
                ),
            },
        )

    provider = _provider_for(
        request, template.cluster_id, cluster.name, cluster.status,
    )

    ref = VMRef(
        provider_type=cluster.provider_type,
        data={"node": template.pve_node, "vmid": template.pve_vmid},
    )
    checks: list[ValidationCheck] = []
    try:
        vm_status = await provider.get_vm_status(ref)
    except ProviderNotFoundError:
        checks.append(ValidationCheck(
            name="vm_exists",
            passed=False,
            message=(
                f"VM {template.pve_vmid} not found on node {template.pve_node}"
            ),
        ))
    else:
        checks.append(ValidationCheck(
            name="vm_exists", passed=True, message="VM found",
        ))
        checks.append(ValidationCheck(
            name="is_template",
            passed=vm_status.is_template,
            message=(
                "VM is a template" if vm_status.is_template
                else f"VM is not a template — run `qm template {template.pve_vmid}`"
            ),
        ))
        checks.append(ValidationCheck(
            name="guest_agent_configured",
            passed=vm_status.guest_agent_configured,
            message=(
                "agent configured in VM settings"
                if vm_status.guest_agent_configured
                else "agent NOT configured (set `agent: 1` in VM config)"
            ),
        ))

    all_passed = all(c.passed for c in checks)

    new_status = "active" if all_passed else "error"
    if template.status != new_status:
        template.status = new_status
        await session.commit()

    return APIResponse(
        data=TemplateValidationResult(
            template_id=template.id, passed=all_passed, checks=checks,
        )
    )


@admin_router.delete(
    "/templates/{template_id}", status_code=204,
)
async def delete_template(
    template_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    template = await session.get(Template, template_id)
    if template is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "template not found"},
        )
    pool_count = await session.scalar(
        select(func.count(Pool.id)).where(Pool.template_id == template_id)
    )
    if pool_count and pool_count > 0:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONFLICT",
                "message": (
                    f"template is in use by {pool_count} pool(s); "
                    "delete or reassign those first"
                ),
            },
        )
    await session.delete(template)
    await session.commit()
