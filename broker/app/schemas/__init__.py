"""Pydantic request/response schemas.

One module per resource, each shipping Create/Update/Read where relevant.
Shared envelope + pagination live in .common; connect-flow and dashboard
shapes are their own modules.
"""
from app.schemas.audit import AuditRead
from app.schemas.cluster import ClusterCreate, ClusterRead, ClusterUpdate
from app.schemas.common import (
    APIResponse,
    ErrorDetail,
    ErrorResponse,
    PaginationParams,
)
from app.schemas.connect import (
    ConnectResponse,
    ConsoleTicketRead,
    NoVNCTicketRead,
)
from app.schemas.dashboard import (
    CapacityResponse,
    DashboardSummary,
    PoolCapacity,
)
from app.schemas.desktop import (
    DesktopCreate,
    DesktopListParams,
    DesktopRead,
    DesktopUpdate,
)
from app.schemas.entitlement import (
    EntitlementCreate,
    EntitlementRead,
    EntitlementUpdate,
)
from app.schemas.pool import PoolCreate, PoolRead, PoolUpdate
from app.schemas.session import SessionCreate, SessionRead, SessionUpdate
from app.schemas.template import TemplateCreate, TemplateRead, TemplateUpdate

__all__ = [
    # common
    "APIResponse",
    "ErrorDetail",
    "ErrorResponse",
    "PaginationParams",
    # cluster
    "ClusterCreate",
    "ClusterRead",
    "ClusterUpdate",
    # template
    "TemplateCreate",
    "TemplateRead",
    "TemplateUpdate",
    # pool
    "PoolCreate",
    "PoolRead",
    "PoolUpdate",
    # desktop
    "DesktopCreate",
    "DesktopListParams",
    "DesktopRead",
    "DesktopUpdate",
    # session
    "SessionCreate",
    "SessionRead",
    "SessionUpdate",
    # entitlement
    "EntitlementCreate",
    "EntitlementRead",
    "EntitlementUpdate",
    # audit
    "AuditRead",
    # connect
    "ConnectResponse",
    "ConsoleTicketRead",
    "NoVNCTicketRead",
    # dashboard
    "CapacityResponse",
    "DashboardSummary",
    "PoolCapacity",
]
