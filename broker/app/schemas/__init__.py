"""Pydantic request/response schemas.

One module per resource, each shipping Create/Update/Read where relevant.
Shared envelope + pagination live in .common; connect-flow and dashboard
shapes are their own modules.
"""
from app.schemas.audit import AuditRead
from app.schemas.cluster import (
    ClusterCreate,
    ClusterRead,
    ClusterReadWithNodes,
    ClusterUpdate,
    NodeInfoRead,
)
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
    RDPTicketRead,
    SpiceTicketRead,
    WebMKSTicketRead,
    ticket_to_wire,
)
from app.schemas.dashboard import (
    CapacitySummary,
    DashboardSummary,
    PoolCapacityWithName,
    PoolSummaryCounts,
    ResourceStatusCounts,
    SessionSummaryCounts,
)
from app.schemas.desktop import (
    DesktopAssignRequest,
    DesktopCreate,
    DesktopListParams,
    DesktopRead,
    DesktopReadDetailed,
    DesktopUpdate,
    TaskAccepted,
)
from app.schemas.entitlement import (
    EntitlementCreate,
    EntitlementRead,
    EntitlementUpdate,
)
from app.schemas.pool import (
    DrainAccepted,
    PoolCapacityDetail,
    PoolCreate,
    PoolDeleteAccepted,
    PoolRead,
    PoolReadDetailed,
    PoolUpdate,
    ProvisionAccepted,
    ProvisionRequest,
)
from app.schemas.session import (
    SessionCreate,
    SessionRead,
    SessionReadAdmin,
    SessionReadDetailed,
    SessionUpdate,
)
from app.schemas.user import (
    UserDesktopView,
    UserPoolView,
    UserSessionView,
)
from app.schemas.template import (
    TemplateCreate,
    TemplateRead,
    TemplateUpdate,
    TemplateValidationResult,
    ValidationCheck,
)

__all__ = [
    # common
    "APIResponse",
    "ErrorDetail",
    "ErrorResponse",
    "PaginationParams",
    # cluster
    "ClusterCreate",
    "ClusterRead",
    "ClusterReadWithNodes",
    "ClusterUpdate",
    "NodeInfoRead",
    # template
    "TemplateCreate",
    "TemplateRead",
    "TemplateUpdate",
    "TemplateValidationResult",
    "ValidationCheck",
    # pool
    "DrainAccepted",
    "PoolCapacityDetail",
    "PoolCreate",
    "PoolDeleteAccepted",
    "PoolRead",
    "PoolReadDetailed",
    "PoolUpdate",
    "ProvisionAccepted",
    "ProvisionRequest",
    # desktop
    "DesktopAssignRequest",
    "DesktopCreate",
    "DesktopListParams",
    "DesktopRead",
    "DesktopReadDetailed",
    "DesktopUpdate",
    "TaskAccepted",
    # session
    "SessionCreate",
    "SessionRead",
    "SessionReadAdmin",
    "SessionReadDetailed",
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
    "RDPTicketRead",
    "SpiceTicketRead",
    "WebMKSTicketRead",
    "ticket_to_wire",
    # user views
    "UserDesktopView",
    "UserPoolView",
    "UserSessionView",
    # dashboard
    "CapacitySummary",
    "DashboardSummary",
    "PoolCapacityWithName",
    "PoolSummaryCounts",
    "ResourceStatusCounts",
    "SessionSummaryCounts",
]
