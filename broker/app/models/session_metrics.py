"""Session metrics ORM model.

Defined for completeness per the schema doc. Not referenced by M2 code
paths — populated later by the in-VM OpenVDI agent.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column


from app.database import Base


class SessionMetrics(Base):
    __tablename__ = "session_metrics"
    __table_args__ = (
        Index("idx_session_metrics_session", "session_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    cpu_percent: Mapped[float | None] = mapped_column(Float)
    memory_percent: Mapped[float | None] = mapped_column(Float)
    disk_io_read: Mapped[int | None] = mapped_column(BigInteger)
    disk_io_write: Mapped[int | None] = mapped_column(BigInteger)
    network_rx: Mapped[int | None] = mapped_column(BigInteger)
    network_tx: Mapped[int | None] = mapped_column(BigInteger)
    active_apps: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
