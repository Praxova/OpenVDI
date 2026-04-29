"""LDAP authentication for OpenVDI.

Implements the bind-as-service-account → search-user → rebind-as-user →
fetch-groups flow per F5 / A3-A6. Returns a canonical username
(lowercase per A4), the user's direct group memberships (per A5 — no
nested-group recursion in v0), and a derived `is_admin` flag.

ldap3 is sync; this module wraps each blocking operation in
asyncio.to_thread() so the event loop isn't blocked during a login.
The login path is not on the hot path (one call per user per ~24h
once refresh tokens are in place) so the per-call thread offload is
negligible cost.
"""
from __future__ import annotations

import asyncio
import logging
import ssl
from dataclasses import dataclass

from fastapi import HTTPException, Request
from ldap3 import ALL, Connection, SUBTREE, Server, Tls
from ldap3.core.exceptions import (
    LDAPBindError,
    LDAPException,
    LDAPInvalidCredentialsResult,
)
from ldap3.utils.conv import escape_filter_chars

from app.config import Settings


logger = logging.getLogger(__name__)


# ── Public surface ────────────────────────────────────────────


class LDAPAuthError(Exception):
    """User-facing auth failure: bad credentials, user not found, etc.

    The login endpoint maps this to HTTP 401 UNAUTHORIZED. The message
    is intentionally generic ("invalid credentials") so the surface
    doesn't leak whether a username exists.
    """


class LDAPServiceError(Exception):
    """Infrastructure failure: LDAP server unreachable, service-account
    bind failed, malformed configuration. The login endpoint maps this
    to HTTP 503 SERVICE_UNAVAILABLE so callers know to retry.
    """


@dataclass(frozen=True)
class LDAPAuthResult:
    username: str            # canonical lowercase per A4
    user_dn: str             # full DN as returned by LDAP
    groups: tuple[str, ...]  # group names (CNs), case preserved
    is_admin: bool           # True iff the admin group is in `groups`


# ── Service ───────────────────────────────────────────────────


class LDAPService:
    """One instance per broker process. Stateless across calls — every
    authenticate() opens a fresh service-account connection, performs
    the search/rebind/group-lookup, and disconnects. No pooling in v0.

    Thread safety: all blocking ldap3 calls are wrapped in
    asyncio.to_thread, so the public `authenticate` coroutine is safe
    to call concurrently from multiple FastAPI request handlers.
    """

    def __init__(self, settings: Settings):
        self._settings = settings

    async def authenticate(
        self, username: str, password: str
    ) -> LDAPAuthResult:
        """Validate credentials against LDAP. Returns LDAPAuthResult on
        success; raises LDAPAuthError or LDAPServiceError on failure.
        """
        # Empty password is the silent-success bug in some LDAP
        # implementations — anonymous bind on empty password masquerades
        # as authentication. Refuse outright.
        if not password:
            raise LDAPAuthError("invalid credentials")
        if not username:
            raise LDAPAuthError("invalid credentials")

        canonical = username.strip().lower()
        return await asyncio.to_thread(
            self._authenticate_blocking, canonical, password
        )

    # ── Private blocking impl ────────────────────────────────

    def _authenticate_blocking(
        self, canonical_username: str, password: str
    ) -> LDAPAuthResult:
        """Sync implementation — runs in a worker thread.

        Three connections are opened in sequence:
          1. service-account bind for the user search
          2. user bind to verify credentials (separate from #1 because
             we may have multiple users in the directory; the search
             tells us *which* DN to bind as)
          3. service-account bind again for group search (the user
             may not have permission to query groups)
        """
        s = self._settings
        server = self._make_server()

        # ── 1. Service-account bind + search ────────────────
        try:
            with self._connect(
                server,
                s.openvdi_ldap_bind_dn,
                s.openvdi_ldap_bind_password.get_secret_value(),
            ) as svc_conn:
                user_dn = self._search_user_dn(svc_conn, canonical_username)
        except LDAPBindError as exc:
            raise LDAPServiceError(
                f"LDAP service-account bind failed: {exc}"
            ) from exc
        except LDAPException as exc:
            raise LDAPServiceError(
                f"LDAP error during user search: {exc}"
            ) from exc

        if user_dn is None:
            # Don't reveal whether the lookup found nothing vs.
            # found-but-bad-password — opaque error.
            logger.info(
                "LDAP login failed: user not found",
                extra={"username": canonical_username},
            )
            raise LDAPAuthError("invalid credentials")

        # ── 2. User bind ────────────────────────────────────
        try:
            with self._connect(server, user_dn, password):
                pass  # bind alone is enough to verify credentials
        except (LDAPBindError, LDAPInvalidCredentialsResult) as exc:
            logger.info(
                "LDAP login failed: bad password",
                extra={
                    "username": canonical_username,
                    "user_dn": user_dn,
                },
            )
            raise LDAPAuthError("invalid credentials") from exc
        except LDAPException as exc:
            raise LDAPServiceError(
                f"LDAP error during user bind: {exc}"
            ) from exc

        # ── 3. Service-account group lookup ─────────────────
        try:
            with self._connect(
                server,
                s.openvdi_ldap_bind_dn,
                s.openvdi_ldap_bind_password.get_secret_value(),
            ) as svc_conn:
                groups = self._search_groups(svc_conn, user_dn)
        except LDAPException as exc:
            raise LDAPServiceError(
                f"LDAP error during group search: {exc}"
            ) from exc

        is_admin = self._is_admin(groups)
        logger.info(
            "LDAP login succeeded",
            extra={
                "username": canonical_username,
                "groups_count": len(groups),
                "is_admin": is_admin,
            },
        )
        return LDAPAuthResult(
            username=canonical_username,
            user_dn=user_dn,
            groups=groups,
            is_admin=is_admin,
        )

    # ── ldap3 plumbing ──────────────────────────────────────

    def _make_server(self) -> Server:
        s = self._settings
        url = s.openvdi_ldap_url
        use_ssl = url.lower().startswith("ldaps://")
        tls = None
        if use_ssl:
            tls = Tls(
                validate=ssl.CERT_REQUIRED if s.openvdi_ldap_verify_ssl
                else ssl.CERT_NONE,
            )
        return Server(url, get_info=ALL, tls=tls)

    def _connect(
        self, server: Server, dn: str, password: str
    ) -> Connection:
        """Return an authenticated, context-managed Connection.

        ldap3's Connection is itself a context manager — `with conn:`
        binds on enter and unbinds on exit. With auto_bind=True the
        bind happens at construction; the `with` form is for cleanup
        symmetry.

        raise_exceptions=True flips ldap3's default silent-failure
        contract: bind/search failures throw LDAPBindError /
        LDAPInvalidCredentialsResult / etc. instead of returning False.
        """
        return Connection(
            server,
            user=dn,
            password=password,
            auto_bind=True,
            raise_exceptions=True,
            read_only=True,
            receive_timeout=10,
        )

    def _search_user_dn(
        self, svc_conn: Connection, canonical_username: str
    ) -> str | None:
        """Search for the user under the configured user_base. Returns
        the DN of the unique match, or None if no match found.

        Multiple matches → infrastructure misconfiguration (the user
        filter is too broad). Surface as LDAPServiceError; treating it
        as auth failure would mask the real bug.
        """
        s = self._settings
        # Escape user input BEFORE filter substitution. A username like
        # "alice)(cn=*)" without escaping would expand to
        # "(sAMAccountName=alice)(cn=*))" — an entirely different filter.
        safe_username = escape_filter_chars(canonical_username)
        ldap_filter = s.openvdi_ldap_user_filter.format(username=safe_username)
        svc_conn.search(
            search_base=s.openvdi_ldap_user_base,
            search_filter=ldap_filter,
            search_scope=SUBTREE,
            attributes=["distinguishedName"],
        )
        if len(svc_conn.entries) == 0:
            return None
        if len(svc_conn.entries) > 1:
            raise LDAPServiceError(
                f"LDAP user search returned {len(svc_conn.entries)} matches "
                f"for {canonical_username!r}; user filter is too broad"
            )
        return svc_conn.entries[0].entry_dn

    def _search_groups(
        self, svc_conn: Connection, user_dn: str
    ) -> tuple[str, ...]:
        """Return the user's direct group memberships per A5. Group
        names are taken from the `cn` attribute; case is preserved as
        the directory returns it.
        """
        s = self._settings
        safe_user_dn = escape_filter_chars(user_dn)
        ldap_filter = s.openvdi_ldap_group_filter.format(user_dn=safe_user_dn)
        svc_conn.search(
            search_base=s.openvdi_ldap_group_base,
            search_filter=ldap_filter,
            search_scope=SUBTREE,
            attributes=["cn"],
        )
        groups: list[str] = []
        for entry in svc_conn.entries:
            cn_attr = entry.cn.value if hasattr(entry, "cn") else None
            if cn_attr is None:
                continue
            # cn comes back as either a single str or a list[str];
            # ldap3 normalizes single-valued attrs to scalars but
            # be defensive.
            if isinstance(cn_attr, list):
                groups.extend(str(x) for x in cn_attr)
            else:
                groups.append(str(cn_attr))
        return tuple(groups)

    def _is_admin(self, groups: tuple[str, ...]) -> bool:
        admin_group = self._settings.openvdi_ldap_admin_group
        if not admin_group:
            return False
        # casefold (not just lower) for Unicode-aware case-insensitive
        # comparison. Admin group config might be entered as
        # "OpenVDI-Admins" or "openvdi-admins"; either matches.
        return any(g.casefold() == admin_group.casefold() for g in groups)

    # ── Lookup-only path (M4-04) ──────────────────────────────

    async def lookup_user(self, canonical_username: str) -> LDAPAuthResult:
        """Look up a user's groups + admin status without verifying
        their password. Used by /auth/refresh to refresh claims without
        re-prompting for credentials.

        Two LDAP roundtrips: service-account search for the user's DN,
        then a group-search filtered to that DN. No user-bind step (the
        refresh cookie already proves the user authenticated successfully
        at some point within the refresh-token TTL).

        Raises:
          LDAPAuthError if the user no longer exists in LDAP (e.g. their
            AD account was deleted between login and refresh).
          LDAPServiceError on any infrastructure failure.
        """
        if not canonical_username:
            raise LDAPAuthError("invalid username")
        return await asyncio.to_thread(
            self._lookup_blocking, canonical_username,
        )

    def _lookup_blocking(
        self, canonical_username: str,
    ) -> LDAPAuthResult:
        """Sync impl — runs in a worker thread.

        Two binds (service-account for user search; service-account again
        for group search). Mirrors `_authenticate_blocking` minus the
        user-bind credential check.
        """
        s = self._settings
        server = self._make_server()

        try:
            with self._connect(
                server,
                s.openvdi_ldap_bind_dn,
                s.openvdi_ldap_bind_password.get_secret_value(),
            ) as svc_conn:
                user_dn = self._search_user_dn(svc_conn, canonical_username)
        except LDAPBindError as exc:
            raise LDAPServiceError(
                f"LDAP service-account bind failed: {exc}"
            ) from exc
        except LDAPException as exc:
            raise LDAPServiceError(
                f"LDAP error during lookup: {exc}"
            ) from exc

        if user_dn is None:
            logger.info(
                "LDAP lookup failed: user no longer exists",
                extra={"username": canonical_username},
            )
            raise LDAPAuthError("user no longer exists in LDAP")

        try:
            with self._connect(
                server,
                s.openvdi_ldap_bind_dn,
                s.openvdi_ldap_bind_password.get_secret_value(),
            ) as svc_conn2:
                groups = self._search_groups(svc_conn2, user_dn)
        except LDAPException as exc:
            raise LDAPServiceError(
                f"LDAP error during group search: {exc}"
            ) from exc

        is_admin = self._is_admin(groups)
        return LDAPAuthResult(
            username=canonical_username,
            user_dn=user_dn,
            groups=groups,
            is_admin=is_admin,
        )


# ── FastAPI dependency factory ────────────────────────────────


def get_ldap_service(request: Request) -> LDAPService:
    """FastAPI dependency. Returns the shared LDAPService from app.state.

    Raises 503 with code AUTH_MODE_NOT_SUPPORTED if the broker is in
    dev mode (ldap_service is None on app.state in dev mode — see
    app/main.py lifespan).
    """
    svc = getattr(request.app.state, "ldap_service", None)
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "AUTH_MODE_NOT_SUPPORTED",
                "message": (
                    "JWT auth endpoints are disabled when "
                    "OPENVDI_AUTH_MODE=dev. Use the M2 X-Dev-* "
                    "header path for local development."
                ),
            },
        )
    return svc
