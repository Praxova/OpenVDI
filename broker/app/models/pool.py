"""Pool ORM model + pool_type / pool_status enums.

A group of desktops provisioned from one template under one cluster.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.cluster import Cluster
    from app.models.desktop import Desktop
    from app.models.entitlement import Entitlement
    from app.models.template import Template


class PoolType(str, enum.Enum):
    PERSISTENT = "persistent"
    NONPERSISTENT = "nonpersistent"


class PoolStatus(str, enum.Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    PROVISIONING = "provisioning"
    ERROR = "error"
    DRAINING = "draining"
    # Set by DELETE /pools/{id}; the pool row is removed once the
    # cascade shim finishes destroying every desktop. See m2-15.
    DELETING = "deleting"


def _pool_type_values(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


class Pool(Base):
    """Desktop pool. CHECK constraints live in the DB schema; not duplicated here."""

    __tablename__ = "pools"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    pool_type: Mapped[PoolType] = mapped_column(
        SQLEnum(
            PoolType,
            name="pool_type",
            create_type=False,
            values_callable=_pool_type_values,
        ),
        nullable=False,
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("templates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    cluster_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("clusters.id", ondelete="RESTRICT"),
        nullable=False,
    )

    min_spare: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    max_size: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("10"))

    vmid_range_start: Mapped[int] = mapped_column(Integer, nullable=False)
    vmid_range_end: Mapped[int] = mapped_column(Integer, nullable=False)

    name_prefix: Mapped[str] = mapped_column(String(32), nullable=False)

    target_nodes: Mapped[str | None] = mapped_column(String(512))
    target_storage: Mapped[str | None] = mapped_column(String(128))

    cpu_cores: Mapped[int | None] = mapped_column(Integer)
    memory_mb: Mapped[int | None] = mapped_column(Integer)

    pve_pool_id: Mapped[str | None] = mapped_column(String(128))

    provider_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"),
    )

    auto_logoff_min: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    delete_on_logoff: Mapped[bool] = mapped_column(Boolean, server_default=text("FALSE"))
    refresh_on_logoff: Mapped[bool] = mapped_column(Boolean, server_default=text("TRUE"))

    status: Mapped[PoolStatus] = mapped_column(
        SQLEnum(
            PoolStatus,
            name="pool_status",
            create_type=False,
            values_callable=_pool_type_values,
        ),
        server_default=text("'active'"),
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
        back_populates="pools", lazy="noload",
    )
    template: Mapped["Template"] = relationship(
        back_populates="pools", lazy="noload",
    )
    desktops: Mapped[list["Desktop"]] = relationship(
        back_populates="pool", lazy="noload",
    )
    entitlements: Mapped[list["Entitlement"]] = relationship(
        back_populates="pool", lazy="noload",
    )
