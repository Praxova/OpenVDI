"""JWT issuance + validation, refresh-secret minting + verification.

This module owns the cryptographic primitives for M4 authentication.
HTTP-layer concerns (cookie names, Set-Cookie headers, request
parsing) live in M4-04's auth endpoints. The split keeps this module
testable without an HTTP fixture.

Key decisions (M4 seed):
- A1 / F3: HS256 access tokens, ≥32-byte secret from Settings.
- F4: 15-minute access TTL, 24-hour refresh TTL. Hardcoded for v0.
- A1 / A8: refresh tokens are random 256-bit secrets; the bcrypt of
  the secret is stored in auth_tokens.refresh_hash. The plaintext
  is sent to the client in the HttpOnly cookie ONCE at issuance time
  and never persisted server-side.
"""
from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Final, Literal
from uuid import UUID

import bcrypt
from fastapi import HTTPException, Request
from jose import JWTError, jwt

from app.config import Settings


# ── Constants per F4 ────────────────────────────────────────

ACCESS_TOKEN_TTL_SECONDS: Final[int] = 15 * 60          # 15 minutes
REFRESH_TOKEN_TTL_SECONDS: Final[int] = 24 * 60 * 60    # 24 hours
JWT_ALGORITHM: Final[str] = "HS256"

# Refresh secret: 32 random bytes → urlsafe-base64 → 43 chars
# (no padding). Within bcrypt's 72-byte input limit by a safe margin.
_REFRESH_SECRET_BYTES: Final[int] = 32

# bcrypt cost factor. 12 is the modern default; ~250ms per hash on
# a current laptop CPU. The login path is rare (once per refresh-TTL)
# so the cost is negligible.
_BCRYPT_ROUNDS: Final[int] = 12


# ── Public types ────────────────────────────────────────────


class InvalidAccessTokenError(Exception):
    """Raised when an access token is malformed, signed with the wrong
    key, expired, missing required claims, or carries an unsupported
    `alg` header. The middleware maps this to 401 UNAUTHORIZED."""


@dataclass(frozen=True)
class AccessTokenClaims:
    """Validated claims of an access token. The middleware constructs
    the request `User` from these."""
    sub: str                            # canonical lowercase username
    groups: tuple[str, ...]             # group names from LDAP
    role: Literal["admin", "user"]
    iat: int                            # issued-at unix timestamp
    exp: int                            # expiry unix timestamp
    jti: UUID                           # auth_tokens row id


@dataclass(frozen=True)
class IssuedRefreshToken:
    """Result of `issue_refresh_token`. Caller persists `hash_str`
    + `expires_at` in `auth_tokens.refresh_hash` / `.expires_at`, and
    sends `secret_str` to the client as the secret half of the
    `<id>.<secret_str>` cookie. The id half is the auth_tokens row's
    UUID, generated at INSERT time by the DB.
    """
    secret_str: str                     # client-facing; 43-char urlsafe-base64
    hash_str: str                       # server-stored; bcrypt of secret_str
    expires_at: datetime                # tz-aware


# ── Service ─────────────────────────────────────────────────


class JWTService:
    """One instance per broker process. Constructed with Settings;
    holds the signing secret in memory for the process lifetime.

    Stateless across calls. Concurrent-call-safe (the only mutable
    state is in `_pwd_context` which passlib documents as thread-safe).
    """

    def __init__(self, settings: Settings):
        # Settings construction already validates ≥32-byte length
        # (M4-02). We don't re-validate here — defense-in-depth would
        # require repeating the check, but a Settings instance with a
        # short secret can't exist in practice, and the redundancy
        # hides the single source of truth.
        self._secret: str = settings.openvdi_jwt_secret.get_secret_value()

    # ── Access tokens ──────────────────────────────────────

    def issue_access_token(
        self,
        *,
        username: str,
        groups: tuple[str, ...],
        is_admin: bool,
        jti: UUID,
        now: datetime | None = None,
    ) -> str:
        """Mint a fresh HS256 access token for `username`.

        The `jti` claim is the auth_tokens row id; access tokens are
        not revoked individually (per F4 — short TTL is the security
        boundary), but `jti` lets log-correlation across multiple
        tokens issued from the same auth session.

        `now` is injectable for tests; production callers leave it None.
        """
        now = now or datetime.now(timezone.utc)
        claims = {
            "sub": username,
            "groups": list(groups),
            "role": "admin" if is_admin else "user",
            "iat": int(now.timestamp()),
            "exp": int(
                (now + timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS)).timestamp()
            ),
            "jti": str(jti),
        }
        return jwt.encode(claims, self._secret, algorithm=JWT_ALGORITHM)

    def validate_access_token(self, token: str) -> AccessTokenClaims:
        """Decode + verify signature + check expiry + check claim shape.

        Raises InvalidAccessTokenError on:
          - malformed token (not three dot-separated base64 segments)
          - signature mismatch (signed with a different key)
          - expired (exp < now)
          - unsupported algorithm in the header
          - missing or wrong-typed required claims
        """
        try:
            # `algorithms` is a HARD pin to HS256 — never accept the
            # `alg: none` masquerade. python-jose respects this list
            # and rejects tokens with any other alg in the header.
            raw = jwt.decode(
                token,
                self._secret,
                algorithms=[JWT_ALGORITHM],
            )
        except JWTError as exc:
            raise InvalidAccessTokenError(f"invalid token: {exc}") from exc

        # Shape check. python-jose's expiry / signature / alg checks
        # already ran inside jwt.decode; here we verify our claims
        # are present and well-typed so the middleware can trust the
        # typed return.
        try:
            sub = raw["sub"]
            groups_raw = raw["groups"]
            role_raw = raw["role"]
            iat = raw["iat"]
            exp = raw["exp"]
            jti_raw = raw["jti"]
        except KeyError as exc:
            raise InvalidAccessTokenError(
                f"missing claim: {exc.args[0]}"
            ) from exc

        if not isinstance(sub, str) or not sub:
            raise InvalidAccessTokenError("invalid claim: sub")
        if not isinstance(groups_raw, list) or not all(
            isinstance(g, str) for g in groups_raw
        ):
            raise InvalidAccessTokenError("invalid claim: groups")
        if role_raw not in ("admin", "user"):
            raise InvalidAccessTokenError("invalid claim: role")
        if not isinstance(iat, int) or not isinstance(exp, int):
            raise InvalidAccessTokenError("invalid claim: iat/exp")
        try:
            jti = UUID(jti_raw)
        except (ValueError, TypeError) as exc:
            raise InvalidAccessTokenError("invalid claim: jti") from exc

        return AccessTokenClaims(
            sub=sub,
            groups=tuple(groups_raw),
            role=role_raw,
            iat=iat,
            exp=exp,
            jti=jti,
        )

    # ── Refresh tokens ─────────────────────────────────────

    def issue_refresh_token(
        self, *, now: datetime | None = None,
    ) -> IssuedRefreshToken:
        """Mint a fresh refresh secret + bcrypt hash + expiry timestamp.

        The caller is responsible for persisting (hash_str, expires_at)
        in `auth_tokens` and sending `secret_str` to the client as the
        secret half of the `<id>.<secret_str>` cookie.
        """
        now = now or datetime.now(timezone.utc)
        raw = secrets.token_bytes(_REFRESH_SECRET_BYTES)  # CSPRNG
        # urlsafe-base64, no padding — keeps the cookie value compact
        # and safe in any URL/header context.
        secret_str = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        hash_bytes = bcrypt.hashpw(
            secret_str.encode("ascii"),
            bcrypt.gensalt(rounds=_BCRYPT_ROUNDS),
        )
        # Store as ASCII string — bcrypt outputs ASCII-safe bytes.
        hash_str = hash_bytes.decode("ascii")
        expires_at = now + timedelta(seconds=REFRESH_TOKEN_TTL_SECONDS)
        return IssuedRefreshToken(
            secret_str=secret_str,
            hash_str=hash_str,
            expires_at=expires_at,
        )

    def verify_refresh_token(self, secret_str: str, hash_str: str) -> bool:
        """Constant-time bcrypt comparison. True iff `secret_str`
        bcrypts to `hash_str`. Returns False on any failure (mismatch,
        malformed hash, etc.) — never raises on bad input.
        """
        if not hash_str:
            return False
        try:
            return bcrypt.checkpw(
                secret_str.encode("ascii"),
                hash_str.encode("ascii"),
            )
        except (ValueError, TypeError):
            # bcrypt.checkpw raises ValueError on malformed hash
            # strings (e.g. truncated rows from a corrupt DB). Treat
            # as verification failure.
            return False


# ── FastAPI dependency factory ────────────────────────────────


def get_jwt_service(request: Request) -> JWTService:
    """FastAPI dependency. Returns the shared JWTService from app.state.

    Raises 503 with code AUTH_MODE_NOT_SUPPORTED if the broker is in
    dev mode (jwt_service is None on app.state in dev mode — see
    app/main.py lifespan).
    """
    svc = getattr(request.app.state, "jwt_service", None)
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
