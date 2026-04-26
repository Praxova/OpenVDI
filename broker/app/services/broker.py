"""Connection brokering service.

`connect` is the heart of the user-facing flow: a signed-in user presses
Connect, we find (or in the persistent first-connect case, provision)
their desktop, start it if it's not running, wait for the guest agent,
issue a console ticket, and return the bundle as a ConnectResult. The
API handler in M2-16 wraps the result in the APIResponse envelope.

`end_session` is the disconnect companion: it flips the session row to
`ended`, clears its connection_info, and releases floating assignments
back to the pool. It does NOT revoke the VNC ticket on Proxmox — tickets
have a short TTL (~2 min) and expire naturally.

Transaction model:
- Everything in `connect` runs in a single AsyncSession transaction,
  including the Step 4 provider calls. The pg_advisory_xact_lock
  acquired in Step 2 is user+pool scoped, hash-keyed, and cheap — the
  blast radius of holding it across network I/O is "one user's
  concurrent double-clicks on the Connect button."
- Exception: the persistent-pool first-connect path calls
  `provision_desktop`, which commits at its own phase boundaries. When
  that happens, the advisory lock releases. A second concurrent
  connect for the same user blocks on the lock until the first call
  commits, then sees the just-provisioned desktop and returns it.
- `end_session` runs in its own short transaction.

No clone-on-demand for non-persistent pools (S-B2). If spares run out
the broker returns PoolFullError → 503; admin provisions more via
`POST /pools/{id}/provision`.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Desktop,
    DesktopStatus,
    Pool,
    PoolType,
    Session,
    SessionStatus,
)
from app.providers.base import (
    ConsoleKind,
    ConsoleTicket,
    HypervisorProvider,
    VMRef,
)
from app.services.audit_service import log_business_event
from app.services.provisioner import provision_desktop
from app.services.session_tracker import (
    transition_to_active,
    transition_to_ended,
)

logger = logging.getLogger(__name__)

# Max seconds to wait for guest agent after starting a stopped desktop.
_AGENT_POLL_TIMEOUT_SECONDS = 60
_AGENT_POLL_INTERVAL_SECONDS = 2.0


class BrokerError(Exception):
    """Generic broker failure (e.g. downstream provision failed)."""


class NotEntitledError(BrokerError):
    """User is not entitled to this pool."""


class PoolFullError(BrokerError):
    """Non-persistent pool has no available desktops."""


class PoolInactiveError(BrokerError):
    """Pool status is not 'active'."""


@dataclass(frozen=True)
class ConnectResult:
    """Return value from `connect()`.

    The API handler maps this to the wire envelope in schemas/connect.py;
    this module stays HTTP-unaware.
    """
    session_id: UUID
    desktop_name: str
    ticket: ConsoleTicket


def _desktop_ref(desktop: Desktop) -> VMRef:
    """Build a VMRef for a Desktop row.

    Module-local helper (vs a method on the ORM model) so models stay
    pure data carriers — see M2-03 scope guardrails. Data shape is
    Proxmox-coupled for v0; a future multi-provider split will lift
    this into providers.base.
    """
    return VMRef(
        provider_type="proxmox",
        data={"node": desktop.pve_node, "vmid": desktop.pve_vmid},
    )


async def _wait_for_agent(
    provider: HypervisorProvider, ref: VMRef,
) -> None:
    """Poll agent_ping until it responds or the timeout elapses.

    Raises BrokerError on timeout — the VM started but something else
    is wrong (half-booted, agent crashed, etc.).
    """
    elapsed = 0.0
    while elapsed < _AGENT_POLL_TIMEOUT_SECONDS:
        if await provider.agent_ping(ref):
            return
        await asyncio.sleep(_AGENT_POLL_INTERVAL_SECONDS)
        elapsed += _AGENT_POLL_INTERVAL_SECONDS
    raise BrokerError("VM started but guest agent unresponsive")


async def connect(
    *,
    session: AsyncSession,
    providers: dict[UUID, HypervisorProvider],
    username: str,
    groups: list[str],
    pool_id: UUID,
) -> ConnectResult:
    """Broker a connection to a desktop in the given pool.

    See module docstring for transaction model. Raises:
      NotEntitledError, PoolFullError, PoolInactiveError,
      BrokerError (downstream failures), ProviderError (from provider).
    """
    # Step 0: load pool + template + cluster, validate active.
    pool = (
        await session.execute(
            select(Pool)
            .options(
                selectinload(Pool.template),
                selectinload(Pool.cluster),
            )
            .where(Pool.id == pool_id)
        )
    ).scalar_one()  # NoResultFound → API maps to 404

    if pool.status != "active":
        raise PoolInactiveError(
            f"pool {pool.name!r} status is {pool.status!r}, not 'active'"
        )

    # Step 1: entitlement check. User-match OR group-match.
    ent_q = text(
        """
        SELECT 1 FROM entitlements
        WHERE pool_id = :pool_id
          AND (
            (principal_type = 'user'  AND principal_name = :username) OR
            (principal_type = 'group' AND principal_name = ANY(CAST(:groups AS text[])))
          )
        LIMIT 1
        """
    )
    ent = (
        await session.execute(
            ent_q,
            {"pool_id": str(pool.id), "username": username, "groups": groups},
        )
    ).scalar()
    if not ent:
        raise NotEntitledError(
            f"user {username!r} is not entitled to pool {pool.name!r}"
        )

    # Step 2: advisory lock per (user, pool). Serializes
    # double-click-happy browsers and the persistent first-connect race.
    lock_key = f"user:{username}:pool:{pool.id}"
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": lock_key},
    )

    provider = providers[pool.cluster_id]

    # Step 3: find or assign a desktop.
    if pool.pool_type == PoolType.PERSISTENT:
        desktop = (
            await session.execute(
                select(Desktop).where(
                    Desktop.pool_id == pool.id,
                    Desktop.assigned_user == username,
                    Desktop.status != DesktopStatus.DELETING,
                )
            )
        ).scalar_one_or_none()

        if desktop is None:
            # First-time-user on this persistent pool. Per S-B2 this is
            # the ONE path where the broker still provisions on demand.
            # provision_desktop commits at phase boundaries; the advisory
            # lock releases but re-concurrent users for the same user
            # will block on the next acquisition and see this desktop.
            desktop = await provision_desktop(
                session=session,
                provider=provider,
                pool=pool,
                template=pool.template,
                assigned_user=username,
            )
            if desktop.status == DesktopStatus.ERROR:
                raise BrokerError(
                    f"provisioning failed: {desktop.error_message}"
                )

    else:  # PoolType.NONPERSISTENT
        # Non-blocking row lock: skip any row another request has
        # already claimed. Two concurrent connects for the last spare
        # get two different outcomes: one row, one PoolFullError.
        desktop = (
            await session.execute(
                select(Desktop)
                .where(
                    Desktop.pool_id == pool.id,
                    Desktop.status == DesktopStatus.AVAILABLE,
                )
                .order_by(Desktop.pve_vmid)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()

        if desktop is None:
            raise PoolFullError(
                f"pool {pool.name!r} has no available desktops; "
                f"admin can provision more via POST /pools/{{id}}/provision"
            )

        desktop.assigned_user = username
        desktop.assignment_type = "floating"

    # Step 4: ensure VM is running.
    ref = _desktop_ref(desktop)
    status = await provider.get_vm_status(ref)
    if status.power_state != "running":
        logger.info(
            "broker: vmid=%d was %s; starting",
            desktop.pve_vmid, status.power_state,
        )
        await provider.wait_for_task(
            await provider.start_vm(ref), timeout_seconds=60,
        )
        await _wait_for_agent(provider, ref)
    desktop.power_state = "running"
    desktop.last_connected = func.now()

    # Step 5: create session row in 'connecting' state.
    # connected_at is set by transition_to_active in Step 7, not at
    # row creation — that way the timestamp reflects when we actually
    # became active, not when the row was reserved.
    session_row = Session(
        desktop_id=desktop.id,
        username=username,
        protocol="novnc",
        status=SessionStatus.CONNECTING,
    )
    session.add(session_row)
    await session.flush()  # need session_row.id for the return

    # Step 6: issue noVNC ticket.
    ticket = await provider.get_console_ticket(ref, ConsoleKind.NOVNC)

    # Step 7: session → active, desktop → connected.
    await transition_to_active(
        session,
        session_row,
        connection_info={
            "kind": "novnc",
            "websocket_url": ticket.websocket_url,
            "password": ticket.password,
            "cert_pem": ticket.cert_pem,
        },
    )
    desktop.status = DesktopStatus.CONNECTED

    # Step 7b: audit the business event inside the same transaction.
    # If anything after this raises before commit, both the state
    # change and the audit row roll back together — which is correct,
    # because no connect actually happened. The HTTP middleware does
    # NOT audit /me/* (W-4), so this is the only record of the event.
    await log_business_event(
        session=session,
        actor=username,
        action="broker.connect",
        resource_type="session",
        resource_id=session_row.id,
        details={
            "pool_id": str(pool.id),
            "pool_name": pool.name,
            "desktop_id": str(desktop.id),
            "desktop_name": desktop.name,
            "assignment_type": desktop.assignment_type,
        },
    )

    await session.commit()

    logger.info(
        "broker: connected username=%s pool=%s desktop=%s session=%s",
        username, pool.name, desktop.name, session_row.id,
    )

    # Step 8: return the bundle.
    return ConnectResult(
        session_id=session_row.id,
        desktop_name=desktop.name,
        ticket=ticket,
    )


async def end_session(
    *,
    session: AsyncSession,
    session_id: UUID,
    actor_username: str | None = None,
) -> None:
    """Transition a session to 'ended' and clear its connection_info.

    Floating assignments (non-persistent pool) release the desktop back
    to 'available' with assigned_user cleared. Persistent assignments
    retain assigned_user; desktop goes 'connected' → 'disconnected'.

    Does NOT call the provider to revoke the VNC ticket — Proxmox tickets
    are short-TTL and expire naturally. Keeping this local-only keeps
    disconnect fast and avoids a dependency on a running provider.

    `actor_username` is currently informational; it could be used by an
    audit hook later to distinguish user self-disconnect from admin
    forced-disconnect.
    """
    session_row = (
        await session.execute(
            select(Session).where(Session.id == session_id)
        )
    ).scalar_one()  # NoResultFound → API maps to 404

    await transition_to_ended(session, session_row)

    # Fetch the desktop for logging. transition_to_ended loaded it into
    # the session's identity map, so session.get() is a cache hit.
    desktop = await session.get(Desktop, session_row.desktop_id)

    # Audit inside the same transaction as the state change — see the
    # `broker.connect` note above for the same rationale.
    await log_business_event(
        session=session,
        actor=actor_username or session_row.username,
        action="broker.session.end",
        resource_type="session",
        resource_id=session_row.id,
        details={
            "desktop_id": str(desktop.id) if desktop else None,
            "desktop_name": desktop.name if desktop else None,
            "assignment_type": desktop.assignment_type if desktop else None,
        },
    )

    await session.commit()
    logger.info(
        "broker: session_id=%s ended (actor=%s) desktop=%s -> %s",
        session_id, actor_username,
        desktop.name if desktop else "?",
        desktop.status.value if desktop else "?",
    )
