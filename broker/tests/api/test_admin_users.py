"""Tests for /admin/users/{username}/* endpoints (M5-01).

Two layers:
  - Service-layer tests against the real DB (via tests/_db.py's
    transactional db_session fixture). Covers the query logic:
    direct vs group entitlements, orphan sessions, include_ended,
    limit, the lowercase-precondition contract.

  - API-handler tests that import the admin handler functions
    directly and call them with a real db_session. Covers the
    case-insensitive lowercase coercion that lives in the API
    layer (the .lower() call in admin_list_user_desktops /
    admin_list_user_sessions) without booting the full broker.

The auth gate (require_admin) is independently covered by
tests/middleware/test_jwt_auth_middleware.py and tests/api/
test_auth_cookies.py — the new admin endpoints inherit the gate
via mounting on `admin_router`. /me/* regression coverage lives in
the existing M2-16 handler tests.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_users import (
    admin_list_user_desktops,
    admin_list_user_sessions,
)
from app.models import (
    Cluster,
    Desktop,
    DesktopStatus,
    Entitlement,
    Pool,
    PoolType,
    Session as SessionModel,
    SessionStatus,
    Template,
)
from app.services.user_diagnostics import (
    list_pools_for_user,
    list_sessions_for_user,
)

# Re-export the transactional db_session fixture (M4-06 pattern).
from tests._db import db_session  # noqa: F401


# ── Service-layer fixture builders ───────────────────────────


async def _make_pool(
    db: AsyncSession,
    *,
    name: str | None = None,
    pool_type: PoolType = PoolType.NONPERSISTENT,
) -> Pool:
    suffix = uuid4().hex[:8]
    cluster = Cluster(
        name=f"c-{suffix}",
        provider_type="proxmox",
        api_url="https://test.example.com:8006",
        token_id="test@pve!unit",
        token_secret="ciphertext-placeholder",
    )
    db.add(cluster)
    await db.flush()

    template = Template(
        cluster_id=cluster.id,
        name=f"tpl-{suffix}",
        pve_vmid=9000,
        pve_node="pve1",
        os_type="windows11",
    )
    db.add(template)
    await db.flush()

    pool = Pool(
        name=name or f"pool-{suffix}",
        display_name=f"Pool {suffix}",
        pool_type=pool_type,
        template_id=template.id,
        cluster_id=cluster.id,
        vmid_range_start=5000,
        vmid_range_end=5099,
        name_prefix="TEST",
    )
    db.add(pool)
    await db.flush()
    return pool


async def _grant(
    db: AsyncSession,
    pool: Pool,
    *,
    principal_type: str,
    principal_name: str,
) -> Entitlement:
    ent = Entitlement(
        pool_id=pool.id,
        principal_type=principal_type,
        principal_name=principal_name,
    )
    db.add(ent)
    await db.flush()
    return ent


async def _make_desktop(
    db: AsyncSession,
    pool: Pool,
    *,
    assigned_user: str | None = None,
    status: DesktopStatus = DesktopStatus.ASSIGNED,
    pve_vmid: int = 5001,
    name: str = "TEST-001",
) -> Desktop:
    desktop = Desktop(
        pool_id=pool.id,
        pve_vmid=pve_vmid,
        pve_node="pve1",
        name=name,
        status=status,
        assigned_user=assigned_user,
        assignment_type="floating" if assigned_user else None,
    )
    db.add(desktop)
    await db.flush()
    return desktop


async def _make_session(
    db: AsyncSession,
    *,
    username: str,
    desktop: Desktop | None,
    status: SessionStatus = SessionStatus.ACTIVE,
    created_at: datetime | None = None,
) -> SessionModel:
    s = SessionModel(
        username=username,
        desktop_id=desktop.id if desktop is not None else None,
        protocol="novnc",
        status=status,
    )
    db.add(s)
    await db.flush()
    if created_at is not None:
        # Override the server-default timestamp when tests need a
        # specific ordering (no onupdate trigger on this column).
        s.created_at = created_at
        await db.flush()
    return s


# ── Service-layer tests: list_pools_for_user ─────────────────


async def test_pools_for_user_direct_entitlement(db_session):
    """Admin variant (groups=None) returns pools entitled by direct
    user match."""
    pool = await _make_pool(db_session)
    await _grant(db_session, pool, principal_type="user", principal_name="alice")
    await db_session.commit()

    result = await list_pools_for_user(
        db_session, username="alice", groups=None,
    )
    assert len(result) == 1
    assert result[0].id == pool.id
    assert result[0].assigned_desktop is None


async def test_pools_for_user_group_entitlement_admin_excludes(db_session):
    """Pools entitled ONLY via group are excluded when groups=None
    (admin variant). The MCP's diagnose tool fetches group
    entitlements separately."""
    pool = await _make_pool(db_session)
    await _grant(
        db_session, pool, principal_type="group", principal_name="VDI-Eng",
    )
    await db_session.commit()

    result = await list_pools_for_user(
        db_session, username="alice", groups=None,
    )
    assert result == []


async def test_pools_for_user_group_entitlement_user_includes(db_session):
    """Same fixture, but user variant (groups=['VDI-Eng']) DOES include
    the group-entitled pool."""
    pool = await _make_pool(db_session)
    await _grant(
        db_session, pool, principal_type="group", principal_name="VDI-Eng",
    )
    await db_session.commit()

    result = await list_pools_for_user(
        db_session, username="alice", groups=["VDI-Eng"],
    )
    assert len(result) == 1
    assert result[0].id == pool.id


async def test_pools_for_user_empty_groups_list_keeps_direct(db_session):
    """`groups=[]` (user with no AD groups) is NOT the same as
    `groups=None`. Direct entitlements still apply."""
    pool = await _make_pool(db_session)
    await _grant(db_session, pool, principal_type="user", principal_name="alice")
    await db_session.commit()

    result = await list_pools_for_user(
        db_session, username="alice", groups=[],
    )
    assert len(result) == 1
    assert result[0].id == pool.id


async def test_pools_for_user_unknown_returns_empty(db_session):
    """Unknown username → empty list, no error (B4)."""
    result = await list_pools_for_user(
        db_session, username="never-existed", groups=None,
    )
    assert result == []


async def test_pools_for_user_includes_assigned_desktop(db_session):
    """Pools with the user holding an assigned desktop surface the
    desktop on the view."""
    pool = await _make_pool(db_session)
    await _grant(db_session, pool, principal_type="user", principal_name="alice")
    desktop = await _make_desktop(db_session, pool, assigned_user="alice")
    await db_session.commit()

    result = await list_pools_for_user(
        db_session, username="alice", groups=None,
    )
    assert len(result) == 1
    assert result[0].assigned_desktop is not None
    assert result[0].assigned_desktop.id == desktop.id


async def test_pools_for_user_excludes_deleting_desktop(db_session):
    """A desktop in `deleting` state is not surfaced as the user's
    assignment — it's mid-tear-down."""
    pool = await _make_pool(db_session)
    await _grant(db_session, pool, principal_type="user", principal_name="alice")
    await _make_desktop(
        db_session, pool, assigned_user="alice",
        status=DesktopStatus.DELETING,
    )
    await db_session.commit()

    result = await list_pools_for_user(
        db_session, username="alice", groups=None,
    )
    assert len(result) == 1
    assert result[0].assigned_desktop is None


# ── Service-layer tests: list_sessions_for_user ──────────────


async def test_sessions_for_user_active_only_by_default(db_session):
    pool = await _make_pool(db_session)
    desktop = await _make_desktop(db_session, pool)
    active = await _make_session(
        db_session, username="alice", desktop=desktop,
        status=SessionStatus.ACTIVE,
    )
    await _make_session(
        db_session, username="alice", desktop=desktop,
        status=SessionStatus.ENDED,
    )
    await db_session.commit()

    result = await list_sessions_for_user(
        db_session, username="alice", include_ended=False, limit=50,
    )
    assert len(result) == 1
    assert result[0].id == active.id


async def test_sessions_for_user_include_ended(db_session):
    pool = await _make_pool(db_session)
    desktop = await _make_desktop(db_session, pool)
    await _make_session(
        db_session, username="alice", desktop=desktop,
        status=SessionStatus.ACTIVE,
    )
    await _make_session(
        db_session, username="alice", desktop=desktop,
        status=SessionStatus.ENDED,
    )
    await db_session.commit()

    result = await list_sessions_for_user(
        db_session, username="alice", include_ended=True, limit=50,
    )
    assert len(result) == 2


async def test_sessions_for_user_orphan_surfaces(db_session):
    """A session whose desktop has been destroyed
    (sessions.desktop_id ON DELETE SET NULL) still surfaces, with
    desktop_id / desktop_name / pool_id / pool_name as None."""
    pool = await _make_pool(db_session)
    desktop = await _make_desktop(db_session, pool)
    s = await _make_session(
        db_session, username="alice", desktop=desktop,
        status=SessionStatus.ENDED,
    )
    # Force-null the FK to simulate the post-destroy state. (Doing
    # the actual desktop delete + cascade is heavier than needed
    # for the assertion.)
    await db_session.execute(
        text("UPDATE sessions SET desktop_id = NULL WHERE id = :id"),
        {"id": s.id},
    )
    await db_session.commit()

    result = await list_sessions_for_user(
        db_session, username="alice", include_ended=True, limit=50,
    )
    assert len(result) == 1
    assert result[0].id == s.id
    assert result[0].desktop_id is None
    assert result[0].desktop_name is None
    assert result[0].pool_id is None
    assert result[0].pool_name is None


async def test_sessions_for_user_unknown_returns_empty(db_session):
    result = await list_sessions_for_user(
        db_session, username="never-existed",
        include_ended=True, limit=50,
    )
    assert result == []


async def test_sessions_for_user_filters_by_username(db_session):
    """Other users' sessions never leak."""
    pool = await _make_pool(db_session)
    desktop = await _make_desktop(db_session, pool)
    await _make_session(db_session, username="alice", desktop=desktop)
    await _make_session(db_session, username="bob", desktop=desktop)
    await db_session.commit()

    result = await list_sessions_for_user(
        db_session, username="alice",
        include_ended=False, limit=50,
    )
    assert len(result) == 1


async def test_sessions_for_user_newest_first(db_session):
    """Ordered by created_at DESC."""
    pool = await _make_pool(db_session)
    desktop = await _make_desktop(db_session, pool)
    now = datetime.now(timezone.utc)
    older = await _make_session(
        db_session, username="alice", desktop=desktop,
        status=SessionStatus.ENDED, created_at=now - timedelta(hours=2),
    )
    newer = await _make_session(
        db_session, username="alice", desktop=desktop,
        status=SessionStatus.ENDED, created_at=now - timedelta(hours=1),
    )
    await db_session.commit()

    result = await list_sessions_for_user(
        db_session, username="alice",
        include_ended=True, limit=50,
    )
    assert [r.id for r in result] == [newer.id, older.id]


async def test_sessions_for_user_respects_limit(db_session):
    pool = await _make_pool(db_session)
    desktop = await _make_desktop(db_session, pool)
    for _ in range(5):
        await _make_session(
            db_session, username="alice", desktop=desktop,
            status=SessionStatus.ENDED,
        )
    await db_session.commit()

    result = await list_sessions_for_user(
        db_session, username="alice",
        include_ended=True, limit=3,
    )
    assert len(result) == 3


# ── API-handler tests (lowercase coercion + envelope shape) ──


async def test_admin_handler_lowercases_username_desktops(db_session):
    """The admin handler canonicalizes the path-param to lowercase
    before delegating to the service. `Alice` and `ALICE` both find
    the entitlement that was stored under `alice`."""
    pool = await _make_pool(db_session)
    await _grant(db_session, pool, principal_type="user", principal_name="alice")
    await db_session.commit()

    for variant in ("alice", "Alice", "ALICE"):
        resp = await admin_list_user_desktops(
            username=variant, session=db_session,
        )
        assert resp.error is None
        ids = [p.id for p in resp.data]
        assert pool.id in ids, f"{variant} should resolve via .lower()"


async def test_admin_handler_lowercases_username_sessions(db_session):
    pool = await _make_pool(db_session)
    desktop = await _make_desktop(db_session, pool)
    s = await _make_session(
        db_session, username="alice", desktop=desktop,
        status=SessionStatus.ACTIVE,
    )
    await db_session.commit()

    for variant in ("alice", "Alice", "ALICE"):
        resp = await admin_list_user_sessions(
            username=variant, include_ended=False, limit=50,
            session=db_session,
        )
        assert resp.error is None
        ids = [r.id for r in resp.data]
        assert s.id in ids, f"{variant} should resolve via .lower()"


async def test_admin_handler_unknown_user_returns_empty_envelope(db_session):
    """B4: unknown user → empty list inside the envelope (200 OK at
    the HTTP layer; here we verify the envelope shape directly)."""
    resp = await admin_list_user_desktops(
        username="never-existed", session=db_session,
    )
    assert resp.error is None
    assert resp.data == []


async def test_admin_handler_excludes_group_entitlements(db_session):
    """The admin handler passes groups=None to the service, so pools
    entitled only via group membership are NOT in the response.
    Per § "Group resolution"."""
    pool = await _make_pool(db_session)
    await _grant(
        db_session, pool, principal_type="group", principal_name="VDI-Eng",
    )
    await db_session.commit()

    resp = await admin_list_user_desktops(
        username="alice", session=db_session,
    )
    assert resp.error is None
    assert resp.data == []
