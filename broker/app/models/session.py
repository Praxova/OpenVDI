"""Session ORM model + session_status enum."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.desktop import Desktop


class SessionStatus(str, enum.Enum):
    CONNECTING = "connecting"
    ACTIVE = "active"
    DISCONNECTED = "disconnected"
    ENDED = "ended"


def _session_status_values(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    # Nullable + ON DELETE SET NULL (M2-15-fix-2): when a desktop is
    # destroyed, its session rows survive with desktop_id=NULL so the
    # continuous-state fields (protocol, durations, future telemetry)
    # remain queryable for M3 user-history views and M4 reporting.
    # See docs/database-schema.md → Notes.
    desktop_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("desktops.id", ondelete="SET NULL"),
        nullable=True,
    )

    username: Mapped[str] = mapped_column(String(256), nullable=False)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False)
    client_ip: Mapped[str | None] = mapped_column(INET)

    status: Mapped[SessionStatus] = mapped_column(
        SQLEnum(
            SessionStatus,
            name="session_status",
            create_type=False,
            values_callable=_session_status_values,
        ),
        server_default=text("'connecting'"),
    )

    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Serialized ConsoleTicket. Cleared on session end so tickets don't
    # outlive the connection they authorize.
    #
    # JSONB(none_as_null=True): when the Python attribute is set to None,
    # write SQL NULL — NOT JSON literal `null`. Without this, SQLAlchemy's
    # default binding stores `'null'::jsonb` and `WHERE connection_info IS
    # NULL` queries miss every cleared row. See docs/database-schema.md →
    # Notes for the convention; transition_to_ended also uses sql_null()
    # at the assignment site as defense-in-depth.
    connection_info: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True),
    )

    # Guest agent telemetry (populated by M4's session monitor worker).
    # `none_as_null=True` is preemptive — if M4 ever clears this on
    # session end, the column already does the right thing. Same
    # convention as connection_info above.
    os_user: Mapped[str | None] = mapped_column(String(256))
    os_info: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True),
    )
    vm_ip_address: Mapped[str | None] = mapped_column(INET)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idle_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )

    desktop: Mapped["Desktop | None"] = relationship(
        back_populates="sessions", lazy="noload",
    )
