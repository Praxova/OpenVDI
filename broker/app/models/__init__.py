"""ORM models package.

Importing this package registers every mapped class on Base.metadata.
"""
from app.database import Base
from app.models.audit import AuditLog
from app.models.cluster import Cluster, ClusterStatus
from app.models.desktop import Desktop, DesktopStatus
from app.models.entitlement import Entitlement
from app.models.pool import Pool, PoolStatus, PoolType
from app.models.session import Session, SessionStatus
from app.models.session_metrics import SessionMetrics
from app.models.template import Template

__all__ = [
    "Base",
    "AuditLog",
    "Cluster",
    "ClusterStatus",
    "Desktop",
    "DesktopStatus",
    "Entitlement",
    "Pool",
    "PoolStatus",
    "PoolType",
    "Session",
    "SessionStatus",
    "SessionMetrics",
    "Template",
]
