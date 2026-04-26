"""Router aggregation points for the broker HTTP surface.

Two routers, each with its own auth gate applied at the router level so
individual route modules in M2-14 through M2-17 don't have to repeat the
Depends(require_admin) / Depends(require_user) decorator on every handler.
This is the single enforcement point for role separation — keep it that
way.

- admin_router: mounted at /api/v1. Requires X-Dev-Role=admin.
- user_router:  mounted at /api/v1/me. Requires authentication only.

If a handler inside admin_router needs a bare `current_user` reference
(e.g. to read the username for an audit log line), it still uses
Depends(current_user) on that specific parameter. The router-level gate
remains the one authorization check.
"""
from fastapi import APIRouter, Depends

from app.services.auth_service import require_admin, require_user


admin_router = APIRouter(dependencies=[Depends(require_admin)])
user_router = APIRouter(dependencies=[Depends(require_user)])
