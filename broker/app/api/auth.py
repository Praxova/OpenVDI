"""Auth API endpoints + cookie helpers.

Cookie posture per A8: HttpOnly + Secure (in jwt mode) + SameSite=Strict,
Path=/api/v1/auth, Max-Age=86400. The Secure flag is omitted when the
broker is in dev mode so localhost HTTP development still works; that
path is unreachable in jwt mode (the dependency factories raise 503
before getting here), but the cookie helpers consult Settings to stay
defensive.

Cookie value format: `<auth_tokens.id>.<secret_str>` where secret_str
is the urlsafe-base64 of the random refresh secret. The id half lets
the server look up the row; the secret half is bcrypt-verified against
`auth_tokens.refresh_hash`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_db_session
from app.models import AuthToken
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.common import APIResponse
from app.services.audit_service import log_business_event
from app.services.jwt_service import (
    ACCESS_TOKEN_TTL_SECONDS,
    REFRESH_TOKEN_TTL_SECONDS,
    JWTService,
    get_jwt_service,
)
from app.services.ldap_service import (
    LDAPAuthError,
    LDAPAuthResult,
    LDAPService,
    LDAPServiceError,
    get_ldap_service,
)


logger = logging.getLogger(__name__)


# ── Cookie constants ────────────────────────────────────────

REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/api/v1/auth"
REFRESH_COOKIE_MAX_AGE = REFRESH_TOKEN_TTL_SECONDS  # seconds


# ── Cookie helpers ──────────────────────────────────────────


def make_refresh_cookie_value(token_id: UUID, secret_str: str) -> str:
    return f"{token_id}.{secret_str}"


def parse_refresh_cookie_value(value: str) -> tuple[UUID, str] | None:
    """Parse `<id>.<secret>` into (UUID, str). Returns None on any
    malformed shape — caller treats None as "not authenticated."

    Splits on the FIRST dot; the secret is urlsafe-base64 which
    cannot contain dots, but a defensive partition guards against
    any future format change.
    """
    if not value:
        return None
    id_str, sep, secret_str = value.partition(".")
    if not sep or not secret_str:
        return None
    try:
        token_id = UUID(id_str)
    except ValueError:
        return None
    return token_id, secret_str


def set_refresh_cookie(
    response: Response,
    *,
    token_id: UUID,
    secret_str: str,
    settings: Settings,
) -> None:
    """Configure the Set-Cookie header for the refresh token. The
    Secure flag is conditional on auth-mode: jwt mode REQUIRES HTTPS
    (per A8 / docs/deploy.md), so the flag is always set; dev mode
    might run on localhost HTTP, where the flag would prevent the
    cookie from being set at all.
    """
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=make_refresh_cookie_value(token_id, secret_str),
        max_age=REFRESH_COOKIE_MAX_AGE,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=not settings.is_dev_auth,
        samesite="strict",
    )


def clear_refresh_cookie(response: Response) -> None:
    """Per A2: send Set-Cookie with Max-Age=0 to clear the cookie.
    Path must match the original (cookies are scoped by path).
    """
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
    )


# ── Router ──────────────────────────────────────────────────

# No router-level dependencies — these endpoints predate authentication
# and must be reachable by unauthenticated clients (otherwise login is
# impossible).
auth_router = APIRouter(prefix="/auth", tags=["auth"])


# ── POST /api/v1/auth/login ────────────────────────────────


@auth_router.post(
    "/login",
    response_model=APIResponse[TokenResponse],
    summary="Authenticate via LDAP and issue access + refresh tokens",
)
async def login(
    body: LoginRequest,
    response: Response,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    ldap: Annotated[LDAPService, Depends(get_ldap_service)],
    jwt_svc: Annotated[JWTService, Depends(get_jwt_service)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> APIResponse[TokenResponse]:
    """Verify credentials against LDAP. On success, INSERT an
    auth_tokens row, mint a refresh secret, set the HttpOnly refresh
    cookie, and return an access token in the response body.

    Errors:
      401 UNAUTHORIZED         — invalid credentials (LDAPAuthError)
      503 SERVICE_UNAVAILABLE  — LDAP unreachable (LDAPServiceError)
      400 INVALID_REQUEST      — malformed body (FastAPI handles)
    """
    client_ip = request.client.host if request.client else None

    # 1. LDAP bind
    try:
        result: LDAPAuthResult = await ldap.authenticate(
            body.username, body.password,
        )
    except LDAPAuthError:
        # Audit failure with the username they typed. May be
        # wrong/forged but useful for forensics; truncate defensively
        # to fit audit_log.actor's column width.
        await log_business_event(
            db,
            actor=body.username[:256],
            action="auth.login.failure",
            details={"reason": "invalid_credentials"},
            client_ip=client_ip,
        )
        await db.commit()
        # Do NOT chain `from exc` here — chaining the LDAPAuthError into
        # __cause__ would leak it to any traceback that escapes the
        # handler.
        raise HTTPException(
            status_code=401,
            detail={
                "code": "UNAUTHORIZED",
                "message": "invalid credentials",
            },
        )
    except LDAPServiceError as exc:
        await log_business_event(
            db,
            actor=body.username[:256],
            action="auth.login.failure",
            details={"reason": "service_unavailable"},
            client_ip=client_ip,
        )
        await db.commit()
        logger.error(
            "LDAP service error during login",
            extra={"username": body.username},
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "SERVICE_UNAVAILABLE",
                "message": (
                    "Authentication service is temporarily unavailable"
                ),
            },
        ) from exc

    # 2. Mint refresh + INSERT auth_tokens row.
    issued = jwt_svc.issue_refresh_token()
    auth_token = AuthToken(
        username=result.username,
        refresh_hash=issued.hash_str.encode("ascii"),
        expires_at=issued.expires_at,
    )
    db.add(auth_token)
    # flush() populates auth_token.id from the server-default
    # gen_random_uuid() without committing. Required because the JWT
    # below needs the id as its `jti` claim.
    await db.flush()

    # 3. Issue access token with jti = auth_tokens row id.
    access_token = jwt_svc.issue_access_token(
        username=result.username,
        groups=result.groups,
        is_admin=result.is_admin,
        jti=auth_token.id,
    )

    # 4. Audit success.
    await log_business_event(
        db,
        actor=result.username,
        action="auth.login.success",
        resource_type="auth_token",
        resource_id=auth_token.id,
        details={
            "groups_count": len(result.groups),
            "is_admin": result.is_admin,
        },
        client_ip=client_ip,
    )
    await db.commit()

    # 5. Set the cookie + return the access token.
    set_refresh_cookie(
        response,
        token_id=auth_token.id,
        secret_str=issued.secret_str,
        settings=settings,
    )
    return APIResponse(
        data=TokenResponse(
            access_token=access_token,
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            role="admin" if result.is_admin else "user",
        ),
    )


# ── POST /api/v1/auth/refresh ──────────────────────────────


async def _revoke_token(db: AsyncSession, token_id: UUID) -> None:
    """Mark the row revoked. Caller commits in their own transaction."""
    await db.execute(
        update(AuthToken)
        .where(
            AuthToken.id == token_id,
            AuthToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(timezone.utc))
    )


@auth_router.post(
    "/refresh",
    response_model=APIResponse[TokenResponse],
    summary="Rotate the refresh token and issue a fresh access token",
)
async def refresh(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    ldap: Annotated[LDAPService, Depends(get_ldap_service)],
    jwt_svc: Annotated[JWTService, Depends(get_jwt_service)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> APIResponse[TokenResponse]:
    """Validate the refresh cookie, re-fetch claims from LDAP, mint a
    new access token + a new refresh secret (rotating the row), and
    update the cookie.

    Errors:
      401 UNAUTHORIZED         — cookie missing/malformed/expired/revoked,
                                 secret mismatch, or user no longer in LDAP
      503 SERVICE_UNAVAILABLE  — LDAP unreachable
    """
    client_ip = request.client.host if request.client else None

    # 1. Parse cookie.
    cookie_value = request.cookies.get(REFRESH_COOKIE_NAME)
    parsed = parse_refresh_cookie_value(cookie_value or "")
    if parsed is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "UNAUTHORIZED",
                "message": "invalid refresh token",
            },
        )
    token_id, secret_str = parsed

    # 2. Look up + lock the row. FOR UPDATE serializes concurrent
    #    refreshes (two browser tabs racing) so only one rotation wins.
    stmt = (
        select(AuthToken)
        .where(AuthToken.id == token_id)
        .with_for_update()
    )
    auth_token = (await db.execute(stmt)).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    # All four "invalid token" branches return the SAME generic 401.
    # Distinguishing them in the response body would leak token-existence
    # information.
    if (
        auth_token is None
        or auth_token.revoked_at is not None
        or auth_token.expires_at < now
    ):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "UNAUTHORIZED",
                "message": "invalid refresh token",
            },
        )

    # 3. Verify the secret bcrypts to the stored hash.
    stored_hash = auth_token.refresh_hash.decode("ascii")
    if not jwt_svc.verify_refresh_token(secret_str, stored_hash):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "UNAUTHORIZED",
                "message": "invalid refresh token",
            },
        )

    # 4. Re-fetch claims from LDAP. Privilege changes (admin
    #    add/remove) propagate within ACCESS_TOKEN_TTL_SECONDS of the
    #    next refresh.
    try:
        result = await ldap.lookup_user(auth_token.username)
    except LDAPAuthError:
        # User was deleted from LDAP between login and refresh —
        # revoke the row and force re-login.
        await _revoke_token(db, token_id)
        await log_business_event(
            db,
            actor=auth_token.username,
            action="auth.refresh.failure",
            details={"reason": "user_no_longer_exists"},
            client_ip=client_ip,
        )
        await db.commit()
        raise HTTPException(
            status_code=401,
            detail={
                "code": "UNAUTHORIZED",
                "message": "invalid refresh token",
            },
        )
    except LDAPServiceError:
        # Don't revoke on infrastructure failure — the user can retry.
        raise HTTPException(
            status_code=503,
            detail={
                "code": "SERVICE_UNAVAILABLE",
                "message": (
                    "Authentication service is temporarily unavailable"
                ),
            },
        )

    # 5. Rotate the refresh secret on the existing row (keep id stable).
    new_issued = jwt_svc.issue_refresh_token()
    auth_token.refresh_hash = new_issued.hash_str.encode("ascii")
    auth_token.expires_at = new_issued.expires_at

    # 6. Issue a new access token. Same jti — access tokens issued
    #    from one auth session all share the row id for log correlation.
    access_token = jwt_svc.issue_access_token(
        username=result.username,
        groups=result.groups,
        is_admin=result.is_admin,
        jti=auth_token.id,
    )

    await log_business_event(
        db,
        actor=result.username,
        action="auth.refresh",
        resource_type="auth_token",
        resource_id=auth_token.id,
        client_ip=client_ip,
    )
    await db.commit()

    # 7. Update the cookie + return.
    set_refresh_cookie(
        response,
        token_id=auth_token.id,
        secret_str=new_issued.secret_str,
        settings=settings,
    )
    return APIResponse(
        data=TokenResponse(
            access_token=access_token,
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            role="admin" if result.is_admin else "user",
        ),
    )


# ── POST /api/v1/auth/logout ──────────────────────────────


@auth_router.post(
    "/logout",
    status_code=204,
    summary="Revoke the refresh token and clear the refresh cookie",
)
async def logout(
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    """Revoke the refresh row (sets revoked_at) and clear the refresh
    cookie. Idempotent — a missing or malformed cookie still produces
    204 No Content. Always clears the cookie even if the row was
    already revoked.

    Logout never returns 4xx. The endpoint's purpose is "make this
    client forget its refresh state," and a missing or malformed
    cookie is consistent with that goal.
    """
    client_ip = request.client.host if request.client else None

    cookie_value = request.cookies.get(REFRESH_COOKIE_NAME)
    parsed = parse_refresh_cookie_value(cookie_value or "")

    actor = "unknown"
    if parsed is not None:
        token_id, _secret_str = parsed
        # Look up to get the username for the audit row. We don't
        # verify the secret — anyone holding the cookie can revoke
        # that token (acceptable; the cookie itself is the auth).
        result = await db.execute(
            select(AuthToken).where(AuthToken.id == token_id)
        )
        auth_token = result.scalar_one_or_none()
        if auth_token is not None and auth_token.revoked_at is None:
            actor = auth_token.username
            auth_token.revoked_at = datetime.now(timezone.utc)

    await log_business_event(
        db,
        actor=actor,
        action="auth.logout",
        client_ip=client_ip,
    )
    await db.commit()

    clear_refresh_cookie(response)
    response.status_code = 204
    return response
