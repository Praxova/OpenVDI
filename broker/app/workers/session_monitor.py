"""SessionMonitorWorker — guest-agent polling + logoff detection.

Iterates desktops in monitored states each tick. For each:
  1. Skip if cluster is offline / maintenance (W6).
  2. Get VM power state. If not running, end session + skip rest.
  3. Poll agent_get_users.
     - Empty → increment per-desktop empty-streak counter.
       At threshold (3), dispatch to refresh / delete / neither.
     - Populated → reset counter, update telemetry.
     - Agent unreachable → reset last_heartbeat, leave streak unchanged.
  4. Network info every 4th tick (60s cadence).

Cadence: 15 seconds (W5). Logoff debounce: 3 consecutive empty polls
(~45s window, W10).

In-memory state:
  - _empty_streaks: dict[UUID, int] tracking per-desktop empty
    agent_get_users polls. Cleared when the user logs back in or the
    desktop transitions out of monitored states (the desktop is then
    no longer iterated).
  - _tick_count: int mod 4 for the 60s network-poll cadence.

Both reset on leader handoff (W11) — the new leader rebuilds them
from observation. Worst case: a user waits an extra 45s for refresh
on a leader change. Acceptable; leader changes are rare.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import ClassVar
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models import (
    Cluster,
    ClusterStatus,
    Desktop,
    DesktopStatus,
    Pool,
    PoolType,
    Session,
    SessionStatus,
)
from app.providers.base import HypervisorProvider, NetworkInterface, VMRef
from app.providers.exceptions import ProviderError, ProviderNotFoundError
from app.services.provisioner import (
    delete_desktop_on_logoff,
    refresh_desktop,
)
from app.services.session_tracker import (
    InvalidSessionStateError,
    transition_to_ended,
)
from app.workers.base import Worker

logger = logging.getLogger(__name__)


# ── Module constants ────────────────────────────────────────

LOGOFF_STREAK_THRESHOLD = 3       # 3 × 15s = 45s before declaring logoff
NETWORK_POLL_EVERY_N_TICKS = 4    # 4 × 15s = 60s cadence

_TELEMETRY_VM_IP_UNCHANGED = "__unchanged__"


class SessionMonitorWorker(Worker):
    name: ClassVar[str] = "session_monitor"
    interval_seconds: ClassVar[float] = 15.0

    def __init__(self) -> None:
        # Per-desktop empty-poll streak. Reset to 0 on populated poll,
        # incremented on empty poll, removed when desktop is no longer
        # iterated (left monitored states).
        self._empty_streaks: dict[UUID, int] = defaultdict(int)
        # Tick counter for the network-poll cadence. Wraps at
        # NETWORK_POLL_EVERY_N_TICKS.
        self._tick_count: int = 0

    async def tick(self, app: FastAPI) -> None:
        self._tick_count = (
            (self._tick_count + 1) % NETWORK_POLL_EVERY_N_TICKS
        )
        poll_network = self._tick_count == 0

        async with async_session_factory() as db:
            rows = await self._fetch_monitored(db)

        # GC streaks for desktops that left monitored states. Without
        # this, a desktop that admin destroyed (etc.) leaves a stale
        # entry in _empty_streaks forever. Bounded memory matters for
        # long-lived broker processes.
        observed_ids = {row[0].id for row in rows}
        for stale_id in list(self._empty_streaks.keys()):
            if stale_id not in observed_ids:
                del self._empty_streaks[stale_id]

        for desktop, pool, cluster in rows:
            try:
                await self._process_desktop(
                    app, desktop, pool, cluster,
                    poll_network=poll_network,
                )
            except Exception:
                # Per-desktop failure must not abort the tick. The
                # WorkerRunner's tick-level exception handler exists
                # but would skip every subsequent desktop in this tick.
                logger.exception(
                    "session monitor failed for desktop",
                    extra={
                        "desktop_id": str(desktop.id),
                        "vmid": desktop.pve_vmid,
                    },
                )

    async def _fetch_monitored(
        self, db: AsyncSession,
    ) -> list[tuple[Desktop, Pool, Cluster]]:
        """Find desktops in monitored states with active clusters.

        Per W6: skip clusters in offline / maintenance / pending. Only
        active clusters. The cluster-config-sync (W13) lives in the
        health_checker worker (M4-11), not here.
        """
        stmt = (
            select(Desktop, Pool, Cluster)
            .join(Pool, Desktop.pool_id == Pool.id)
            .join(Cluster, Pool.cluster_id == Cluster.id)
            .where(Desktop.status.in_([
                DesktopStatus.ASSIGNED,
                DesktopStatus.CONNECTED,
                DesktopStatus.DISCONNECTED,
            ]))
            .where(Cluster.status == ClusterStatus.ACTIVE.value)
        )
        result = await db.execute(stmt)
        return list(result.all())

    async def _process_desktop(
        self,
        app: FastAPI,
        desktop: Desktop,
        pool: Pool,
        cluster: Cluster,
        *,
        poll_network: bool,
    ) -> None:
        """Drive the per-desktop state machine for one tick."""
        provider = self._provider_for(app, cluster)
        if provider is None:
            return  # cluster's provider not registered on this broker

        ref = VMRef(
            provider_type=provider.provider_type,
            data={"node": desktop.pve_node, "vmid": desktop.pve_vmid},
        )

        # 1. Power state.
        try:
            vm_status = await provider.get_vm_status(ref)
        except ProviderNotFoundError:
            # VM gone in Proxmox — admin destroyed via qm or provider
            # is misconfigured. End any active session + leave desktop
            # in error state for admin investigation. Don't recycle.
            await self._end_orphaned_session(desktop)
            return

        if vm_status.power_state != "running":
            # VM stopped: end session, do NOT trigger refresh/delete.
            # session-tracking.md → "Skip remaining checks."
            async with async_session_factory() as db:
                session_row = await self._latest_active_session(
                    db, desktop.id,
                )
                if session_row is not None:
                    try:
                        await transition_to_ended(db, session_row)
                        await db.commit()
                    except InvalidSessionStateError:
                        # Race: another caller ended this session
                        # between our SELECT and our transition. Safe
                        # to ignore — the desired state was reached.
                        await db.rollback()
            self._empty_streaks.pop(desktop.id, None)
            return

        # 2. Find the latest active session.
        async with async_session_factory() as db:
            session_row = await self._latest_active_session(db, desktop.id)
        if session_row is None:
            # Desktop in monitored status but no active session — M2
            # inconsistency or admin force-disconnect that didn't
            # update desktop status. Don't dispatch logoff; admin
            # handles.
            return

        # 3. Poll guest-agent users.
        try:
            users = await provider.agent_get_users(ref)
        except ProviderError:
            # Agent unreachable. Per session-tracking.md: clear
            # last_heartbeat (signals "we don't know"), don't
            # increment streak.
            async with async_session_factory() as db:
                refreshed = await db.get(Session, session_row.id)
                if refreshed is not None:
                    refreshed.last_heartbeat = None
                    await db.commit()
            return

        if users:
            # User logged in → reset streak, update telemetry.
            self._empty_streaks.pop(desktop.id, None)
            await self._update_telemetry(
                provider, ref, desktop, session_row,
                users=users, poll_network=poll_network,
            )
            return

        # Empty users — handle debounce.
        if session_row.status != SessionStatus.ACTIVE:
            # Per design pin: only ACTIVE sessions trigger logoff.
            # CONNECTING (broker mid-connect, agent not yet started)
            # and DISCONNECTED (portal closed but VM running) are
            # observed but don't dispatch.
            return

        self._empty_streaks[desktop.id] += 1
        streak = self._empty_streaks[desktop.id]
        logger.debug(
            "empty users",
            extra={
                "desktop_id": str(desktop.id),
                "vmid": desktop.pve_vmid,
                "streak": streak,
            },
        )
        if streak < LOGOFF_STREAK_THRESHOLD:
            return

        # Threshold reached → dispatch logoff.
        del self._empty_streaks[desktop.id]
        logger.info(
            "logoff detected",
            extra={
                "desktop_id": str(desktop.id),
                "vmid": desktop.pve_vmid,
                "pool": pool.name,
                "pool_type": pool.pool_type.value,
                "refresh_on_logoff": pool.refresh_on_logoff,
                "delete_on_logoff": pool.delete_on_logoff,
            },
        )
        await self._handle_logoff(
            app=app, provider=provider,
            desktop=desktop, pool=pool, session_row=session_row,
        )

    # ── Logoff dispatch ────────────────────────────────────

    async def _handle_logoff(
        self,
        *,
        app: FastAPI,
        provider: HypervisorProvider,
        desktop: Desktop,
        pool: Pool,
        session_row: Session,
    ) -> None:
        """Dispatch to refresh / delete / end-only based on pool config.

        End the session first via transition_to_ended (which also
        flips the desktop to AVAILABLE on the floating path), then
        dispatch refresh / delete. The brief window between the two
        commits is documented in M4-08's design notes — a different
        user could in theory grab the just-freed desktop in the
        ~200ms gap. Acceptable v0 trade-off; M5+ closes the gap with
        an intermediate desktop status.
        """
        is_non_persistent = pool.pool_type == PoolType.NONPERSISTENT

        async with async_session_factory() as db:
            refreshed_session = await db.get(Session, session_row.id)
            if refreshed_session is None:
                return  # raced; session was deleted under us
            try:
                await transition_to_ended(db, refreshed_session)
                await db.commit()
            except InvalidSessionStateError:
                # Already ended by another caller. Treat as success.
                await db.rollback()

        if not is_non_persistent:
            # Persistent: end-of-session + retain assignment.
            # transition_to_ended already set desktop=DISCONNECTED.
            return

        # Non-persistent: branch on flags. delete_on_logoff wins over
        # refresh_on_logoff if both are set (defensive — admin
        # shouldn't set both, but if they do, delete is the more
        # destructive action and follows the principle of "do what
        # the operator asked first").
        if pool.delete_on_logoff:
            async with async_session_factory() as db:
                await delete_desktop_on_logoff(
                    session=db, provider=provider,
                    desktop_id=desktop.id,
                )
        elif pool.refresh_on_logoff:
            async with async_session_factory() as db:
                await refresh_desktop(
                    session=db, provider=provider,
                    desktop_id=desktop.id,
                )
        # Non-persistent with neither flag: transition_to_ended
        # already set desktop=AVAILABLE for floating, cleared
        # assigned_user. Admin manages from here.

    # ── Telemetry ──────────────────────────────────────────

    async def _update_telemetry(
        self,
        provider: HypervisorProvider,
        ref: VMRef,
        desktop: Desktop,
        session_row: Session,
        *,
        users: list,
        poll_network: bool,
    ) -> None:
        """Update session.os_user, last_heartbeat, optionally vm_ip_address.
        """
        os_user = users[0].username if users else None

        # Optional network poll (every NETWORK_POLL_EVERY_N_TICKS ticks).
        vm_ip: str | None | str = None
        if poll_network:
            try:
                interfaces = await provider.agent_get_network(ref)
                vm_ip = self._pick_primary_ip(interfaces)
            except ProviderError:
                # Soft failure — keep prior vm_ip_address.
                vm_ip = _TELEMETRY_VM_IP_UNCHANGED

        async with async_session_factory() as db:
            row = await db.get(Session, session_row.id)
            if row is None:
                return
            row.last_heartbeat = datetime.now(timezone.utc)
            row.os_user = os_user
            if vm_ip != _TELEMETRY_VM_IP_UNCHANGED and poll_network:
                row.vm_ip_address = vm_ip
            await db.commit()

        # Mismatch detection — log only, don't break flow. Casefold
        # (Unicode-aware) since AD canonicalizes lowercase per A4
        # (M4-02); the os_user from agent_get_users is OS-reported and
        # may have varying case.
        if (
            os_user is not None
            and desktop.assigned_user is not None
            and os_user.casefold() != desktop.assigned_user.casefold()
        ):
            logger.warning(
                "os_user mismatch on desktop",
                extra={
                    "desktop_id": str(desktop.id),
                    "vmid": desktop.pve_vmid,
                    "assigned_user": desktop.assigned_user,
                    "os_user": os_user,
                },
            )

    @staticmethod
    def _pick_primary_ip(
        interfaces: list[NetworkInterface],
    ) -> str | None:
        """Return the first non-loopback IPv4 address found across the
        agent-reported interfaces. None if no usable IP. v0 ignores
        IPv6; M5+ may broaden if real users want it.
        """
        for iface in interfaces:
            if not iface.is_up:
                continue
            for ip in iface.ip_addresses:
                if ip.startswith("127.") or ip == "::1" or ":" in ip:
                    continue  # skip loopback + IPv6
                return ip
        return None

    # ── Helpers ───────────────────────────────────────────

    async def _latest_active_session(
        self, db: AsyncSession, desktop_id: UUID,
    ) -> Session | None:
        """Find the latest CONNECTING / ACTIVE / DISCONNECTED session.
        ENDED sessions don't count — they're terminal.
        """
        stmt = (
            select(Session)
            .where(Session.desktop_id == desktop_id)
            .where(Session.status.in_([
                SessionStatus.CONNECTING,
                SessionStatus.ACTIVE,
                SessionStatus.DISCONNECTED,
            ]))
            .order_by(Session.created_at.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def _end_orphaned_session(self, desktop: Desktop) -> None:
        """VM gone in Proxmox but desktop row still exists. End any
        active session for the desktop; leave the desktop row alone
        (admin investigates / runs DELETE /desktops/{id}).
        """
        async with async_session_factory() as db:
            session_row = await self._latest_active_session(db, desktop.id)
            if session_row is not None:
                try:
                    await transition_to_ended(db, session_row)
                    await db.commit()
                except InvalidSessionStateError:
                    await db.rollback()
        self._empty_streaks.pop(desktop.id, None)

    @staticmethod
    def _provider_for(
        app: FastAPI, cluster: Cluster,
    ) -> HypervisorProvider | None:
        """Look up the provider for the cluster from app.state.providers.
        Returns None if the cluster isn't registered on this broker —
        lifespan startup may have skipped it (W13 propagates updates
        from M4-11's health_checker).
        """
        providers = getattr(app.state, "providers", None) or {}
        return providers.get(cluster.id)
