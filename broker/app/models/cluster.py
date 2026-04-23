"""Cluster ORM model.

A registered hypervisor cluster. Matches clusters table in
docs/database-schema.md.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.pool import Pool
    from app.models.template import Template


class Cluster(Base):
    """Hypervisor cluster registration.

    `status` values: 'pending' (post-register pre-first-ping), 'active'
    (last ping OK), 'maintenance' (admin-disabled), 'offline' (last
    ping failed). Stored as VARCHAR(32), not a Postgres enum — see
    M2-01 decision log.
    """

    __tablename__ = "clusters"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)

    # Matches HypervisorProvider.provider_type. 'proxmox' in v0.
    provider_type: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'proxmox'"),
    )

    api_url: Mapped[str] = mapped_column(String(512), nullable=False)
    token_id: Mapped[str] = mapped_column(String(256), nullable=False)
    # Fernet ciphertext (bytes as UTF-8 str). Encrypt before assigning;
    # decrypt only when instantiating a provider.
    token_secret: Mapped[str] = mapped_column(String(256), nullable=False)

    verify_ssl: Mapped[bool] = mapped_column(
        Boolean, server_default=text("TRUE"),
    )
    node_filter: Mapped[str | None] = mapped_column(String(512))

    provider_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"),
    )

    status: Mapped[str] = mapped_column(
        String(32), server_default=text("'pending'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    templates: Mapped[list["Template"]] = relationship(
        back_populates="cluster", lazy="noload",
    )
    pools: Mapped[list["Pool"]] = relationship(
        back_populates="cluster", lazy="noload",
    )
