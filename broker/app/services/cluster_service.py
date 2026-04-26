"""Cluster lifecycle helpers: provider construction + ping/status update.

Single construction point for `HypervisorProvider` instances and the
single writer of `clusters.status`. The lifespan handler, the `PUT
/clusters/{id}` rotation path, and any future caller (M4 health-checker
worker, conformance suite, an explicit `POST /clusters/{id}/ping`
endpoint) all route through here so provider wiring and audit behavior
have exactly one shape.

Two functions:
  construct_provider(cluster) — build a HypervisorProvider from a row.
  ping_and_update_status(cluster_id, provider) — ping, settle status,
    write a `cluster.ping.flipped` audit row only when the status
    actually changes.

`ping_and_update_status` opens its own DB session deliberately. Callers
pass a cluster_id (not a row), and the helper does its own load/update/
commit cycle. That keeps the helper safe to run from detached contexts:
the lifespan calls it as `create_task(...)` after its own session has
closed, and the PUT handler calls it after committing the credential
update.
"""
from __future__ import annotations

import logging
from uuid import UUID

from app.crypto import decrypt_secret
from app.database import async_session_factory
from app.models.cluster import Cluster, ClusterStatus
from app.providers import get_provider_class
from app.providers.base import HypervisorProvider
from app.providers.exceptions import ProviderError


logger = logging.getLogger(__name__)


async def construct_provider(cluster: Cluster) -> HypervisorProvider:
    """Build a live `HypervisorProvider` instance from a Cluster row.

    Decrypts `token_secret` and hands it to the provider class
    constructor. Does NOT ping — `ping_and_update_status` is the next
    step. Raises:
      ValueError       — unknown `provider_type`.
      Exception        — anything else from decrypt or constructor
                         (malformed key, bad provider_config, etc.) —
                         caller decides how to surface.

    The single construction point. Anything that needs a provider
    instance routes through here so credential decryption,
    constructor-argument shape, and future provider_config plumbing
    have exactly one home.
    """
    provider_cls = get_provider_class(cluster.provider_type)
    plaintext = decrypt_secret(cluster.token_secret)
    return provider_cls(
        api_url=cluster.api_url,
        token_id=cluster.token_id,
        token_secret=plaintext,
        verify_ssl=cluster.verify_ssl,
    )


async def ping_and_update_status(
    cluster_id: UUID,
    provider: HypervisorProvider,
) -> ClusterStatus:
    """Ping a provider; persist the resulting status on the Cluster row.

    Caller passes the cluster id (NOT a row) and a provider instance;
    this helper opens its own session, loads the row, pings, updates
    status, optionally writes an audit row, and commits.

    Returns the new ClusterStatus the row was set to. If the row
    vanished between call and update, returns the value that *would*
    have been applied (caller usually ignores this case).

    Writes `cluster.ping.flipped` audit ONLY when status actually
    changes. The lifespan re-pings every cluster on every restart;
    auditing same-status pings would flood the log.

    Catches both `ProviderError` and broad `Exception` — this helper
    is a safety net for startup tasks and detached PUT continuations
    that shouldn't be brought down by an unforeseen ping failure.
    """
    async with async_session_factory() as session:
        cluster = await session.get(Cluster, cluster_id)
        if cluster is None:
            logger.warning(
                "cluster %s vanished before ping completed", cluster_id,
            )
            # Caller almost never uses the return when the row is gone;
            # offline is the conservative default.
            return ClusterStatus.OFFLINE

        previous_value = cluster.status

        try:
            ok = await provider.ping()
        except ProviderError as exc:
            logger.warning(
                "cluster ping raised for %s: %s: %s",
                cluster.name, type(exc).__name__, exc,
            )
            new_status = ClusterStatus.OFFLINE
        except Exception:
            logger.exception(
                "unexpected ping failure for cluster %s", cluster.name,
            )
            new_status = ClusterStatus.OFFLINE
        else:
            new_status = (
                ClusterStatus.ACTIVE if ok else ClusterStatus.OFFLINE
            )

        cluster.status = new_status.value

        if previous_value != new_status.value:
            # Deferred import — audit_service in turn imports models, which
            # can race the cluster_service import in some loading orders.
            from app.services.audit_service import log_business_event

            await log_business_event(
                session=session,
                actor="system",
                action="cluster.ping.flipped",
                resource_type="cluster",
                resource_id=cluster_id,
                details={
                    "cluster_name": cluster.name,
                    "from": previous_value,
                    "to": new_status.value,
                },
            )

        await session.commit()
        logger.info(
            "ping: cluster %s → %s", cluster.name, new_status.value,
        )
        return new_status
