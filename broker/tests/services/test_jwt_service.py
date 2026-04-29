"""Unit tests for JWTService.

No database. No HTTP. Each test exercises one method with explicit
inputs and verifies the output shape + behavior.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from jose import jwt as jose_jwt

from app.services.jwt_service import (
    ACCESS_TOKEN_TTL_SECONDS,
    REFRESH_TOKEN_TTL_SECONDS,
    AccessTokenClaims,
    InvalidAccessTokenError,
    IssuedRefreshToken,
    JWTService,
)


SECRET = "x" * 64  # 64 bytes — passes the M4-02 ≥32 floor


@pytest.fixture
def jwt_settings(monkeypatch_settings):
    """Settings with auth_mode=jwt and plausible LDAP fields. The LDAP
    fields aren't used by JWTService but must be set for Settings to
    construct under jwt mode (M4-02's model validator).
    """
    return monkeypatch_settings(
        OPENVDI_AUTH_MODE="jwt",
        OPENVDI_JWT_SECRET=SECRET,
        OPENVDI_LDAP_URL="ldap://test",
        OPENVDI_LDAP_BIND_DN="cn=svc",
        OPENVDI_LDAP_BIND_PASSWORD="x",
        OPENVDI_LDAP_USER_BASE="ou=u",
        OPENVDI_LDAP_GROUP_BASE="ou=g",
        OPENVDI_LDAP_ADMIN_GROUP="Admins",
        OPENVDI_PORTAL_ORIGIN="https://test.example.com",
    )


@pytest.fixture
def jwt_service(jwt_settings):
    return JWTService(jwt_settings)


# ── Access token tests ───────────────────────────────────────


def test_issue_and_validate_access_token_round_trip(jwt_service):
    """Issue a token, decode it, claims match what we put in.

    `now` is set ~5 minutes in the past so iat lands in the recent
    past (realistic) but exp (now + 15 min) is still in the future
    when validation runs. Avoids a far-future `now` that would make
    iat look weird in logs.
    """
    jti = uuid4()
    now = datetime.now(timezone.utc) - timedelta(minutes=5)
    token = jwt_service.issue_access_token(
        username="alice",
        groups=("Engineering", "VPN-Users"),
        is_admin=False,
        jti=jti,
        now=now,
    )
    claims = jwt_service.validate_access_token(token)
    assert isinstance(claims, AccessTokenClaims)
    assert claims.sub == "alice"
    assert claims.groups == ("Engineering", "VPN-Users")
    assert claims.role == "user"
    assert claims.jti == jti
    assert claims.iat == int(now.timestamp())
    assert claims.exp == int(now.timestamp()) + ACCESS_TOKEN_TTL_SECONDS


def test_issue_access_token_admin_role(jwt_service):
    """is_admin=True produces role='admin' claim."""
    token = jwt_service.issue_access_token(
        username="alice", groups=(), is_admin=True, jti=uuid4(),
    )
    claims = jwt_service.validate_access_token(token)
    assert claims.role == "admin"


def test_validate_access_token_rejects_expired(jwt_service):
    """Token with exp < now raises InvalidAccessTokenError."""
    expired_now = datetime.now(timezone.utc) - timedelta(
        seconds=ACCESS_TOKEN_TTL_SECONDS + 60
    )
    token = jwt_service.issue_access_token(
        username="alice", groups=(), is_admin=False,
        jti=uuid4(), now=expired_now,
    )
    with pytest.raises(InvalidAccessTokenError):
        jwt_service.validate_access_token(token)


def test_validate_access_token_rejects_wrong_signature(jwt_service):
    """Token signed with a different key fails."""
    forged = jose_jwt.encode(
        {
            "sub": "alice", "groups": [], "role": "user",
            "iat": 0, "exp": 9_999_999_999, "jti": str(uuid4()),
        },
        "different-secret-of-sufficient-length-32-bytes-here",
        algorithm="HS256",
    )
    with pytest.raises(InvalidAccessTokenError):
        jwt_service.validate_access_token(forged)


def test_validate_access_token_rejects_alg_none(jwt_service):
    """Token with `alg: none` (no signature) is rejected — the
    algorithm pin in jwt.decode prevents the alg-substitution attack.
    """
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "none", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps({
            "sub": "alice", "groups": [], "role": "user",
            "iat": 0, "exp": 9_999_999_999, "jti": str(uuid4()),
        }).encode()
    ).rstrip(b"=").decode()
    forged = f"{header}.{payload}."  # empty signature
    with pytest.raises(InvalidAccessTokenError):
        jwt_service.validate_access_token(forged)


def test_validate_access_token_rejects_malformed(jwt_service):
    """Garbage input is rejected without crashing."""
    with pytest.raises(InvalidAccessTokenError):
        jwt_service.validate_access_token("not-a-jwt")
    with pytest.raises(InvalidAccessTokenError):
        jwt_service.validate_access_token("")
    with pytest.raises(InvalidAccessTokenError):
        jwt_service.validate_access_token("a.b.c")


def test_validate_access_token_rejects_missing_required_claim(jwt_service):
    """A token missing a required claim (e.g. `jti`) is rejected."""
    incomplete = jose_jwt.encode(
        {
            "sub": "alice", "groups": [], "role": "user",
            "iat": 0, "exp": 9_999_999_999,  # no jti
        },
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(InvalidAccessTokenError, match="jti"):
        jwt_service.validate_access_token(incomplete)


def test_validate_access_token_rejects_invalid_role_value(jwt_service):
    """A token with role='superuser' is rejected — the literal type is
    enforced server-side, even though we issue only 'admin'/'user'."""
    bogus = jose_jwt.encode(
        {
            "sub": "alice", "groups": [], "role": "superuser",
            "iat": 0, "exp": 9_999_999_999, "jti": str(uuid4()),
        },
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(InvalidAccessTokenError, match="role"):
        jwt_service.validate_access_token(bogus)


# ── Refresh token tests ─────────────────────────────────────


def test_issue_refresh_token_returns_distinct_secrets(jwt_service):
    """Two consecutive issuances produce different secrets — CSPRNG sanity."""
    a = jwt_service.issue_refresh_token()
    b = jwt_service.issue_refresh_token()
    assert isinstance(a, IssuedRefreshToken)
    assert a.secret_str != b.secret_str
    assert a.hash_str != b.hash_str


def test_issue_refresh_token_expires_at_is_now_plus_ttl(jwt_service):
    """expires_at = now + REFRESH_TOKEN_TTL_SECONDS, exact match."""
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
    issued = jwt_service.issue_refresh_token(now=now)
    expected = now + timedelta(seconds=REFRESH_TOKEN_TTL_SECONDS)
    assert issued.expires_at == expected


def test_verify_refresh_token_round_trip(jwt_service):
    """Verify accepts the secret that was issued."""
    issued = jwt_service.issue_refresh_token()
    assert jwt_service.verify_refresh_token(
        issued.secret_str, issued.hash_str
    ) is True


def test_verify_refresh_token_rejects_wrong_secret(jwt_service):
    """Verify rejects a secret that wasn't bcrypted into the hash."""
    issued = jwt_service.issue_refresh_token()
    assert jwt_service.verify_refresh_token(
        "not-the-secret", issued.hash_str
    ) is False


def test_verify_refresh_token_returns_false_on_malformed_hash(jwt_service):
    """A corrupted hash string makes verify return False, not raise."""
    issued = jwt_service.issue_refresh_token()
    assert jwt_service.verify_refresh_token(
        issued.secret_str, "garbage"
    ) is False
    assert jwt_service.verify_refresh_token(
        issued.secret_str, ""
    ) is False
    assert jwt_service.verify_refresh_token(
        issued.secret_str, "$2b$12$short"
    ) is False


def test_refresh_secret_is_urlsafe_base64(jwt_service):
    """secret_str contains only urlsafe-base64 characters and is the
    expected length for 32 bytes (43 chars unpadded)."""
    issued = jwt_service.issue_refresh_token()
    assert re.fullmatch(r"[A-Za-z0-9_-]+", issued.secret_str)
    assert len(issued.secret_str) == 43
