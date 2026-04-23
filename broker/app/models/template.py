"""Template ORM model.

A golden image registered within a cluster. Pool desktops are cloned
from templates.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.cluster import Cluster
    from app.models.pool import Pool


class Template(Base):
    __tablename__ = "templates"
    __table_args__ = (
        UniqueConstraint("cluster_id", "pve_vmid", name="templates_cluster_vmid_uq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    cluster_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("clusters.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)

    # Proxmox provider: VMID + node where the template lives.
    pve_vmid: Mapped[int] = mapped_column(Integer, nullable=False)
    pve_node: Mapped[str] = mapped_column(String(128), nullable=False)

    os_type: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    cpu_cores: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("2"))
    memory_mb: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("4096"))
    disk_gb: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("60"))
    gpu_required: Mapped[bool] = mapped_column(Boolean, server_default=text("FALSE"))

    tags: Mapped[list[Any]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"),
    )
    provider_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"),
    )

    status: Mapped[str] = mapped_column(
        String(32), server_default=text("'active'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    cluster: Mapped["Cluster"] = relationship(
        back_populates="templates", lazy="noload",
    )
    pools: Mapped[list["Pool"]] = relationship(
        back_populates="template", lazy="noload",
    )
