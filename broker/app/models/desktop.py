"""Desktop ORM model + desktop_status enum."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.pool import Pool
    from app.models.session import Session


class DesktopStatus(str, enum.Enum):
    PROVISIONING = "provisioning"
    AVAILABLE = "available"
    ASSIGNED = "assigned"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    DELETING = "deleting"
    MAINTENANCE = "maintenance"


def _desktop_status_values(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


class Desktop(Base):
    __tablename__ = "desktops"
    __table_args__ = (
        UniqueConstraint("pve_vmid", name="desktops_pve_vmid_uq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    pool_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("pools.id", ondelete="RESTRICT"),
        nullable=False,
    )

    pve_vmid: Mapped[int] = mapped_column(Integer, nullable=False)
    pve_node: Mapped[str] = mapped_column(String(128), nullable=False)

    name: Mapped[str] = mapped_column(String(256), nullable=False)

    # AD username or null while unassigned. 'floating' assignments clear
    # this on session end; 'persistent' preserve it.
    assigned_user: Mapped[str | None] = mapped_column(String(256))
    assignment_type: Mapped[str | None] = mapped_column(String(32))

    status: Mapped[DesktopStatus] = mapped_column(
        SQLEnum(
            DesktopStatus,
            name="desktop_status",
            create_type=False,
            values_callable=_desktop_status_values,
        ),
        server_default=text("'provisioning'"),
    )
    power_state: Mapped[str] = mapped_column(
        String(32), server_default=text("'stopped'"),
    )
    spice_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("FALSE"))

    last_connected: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_disconnected: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provisioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    pve_task_upid: Mapped[str | None] = mapped_column(String(512))
    # Kind of the in-flight task (matches DesktopTaskKind values). NULL
    # when no task is in flight. Persisted so startup-resume knows which
    # completion handler to invoke — see app.services.task_tracker.
    pve_task_kind: Mapped[str | None] = mapped_column(String(32))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    pool: Mapped["Pool"] = relationship(
        back_populates="desktops", lazy="noload",
    )
    sessions: Mapped[list["Session"]] = relationship(
        back_populates="desktop", lazy="noload",
    )
