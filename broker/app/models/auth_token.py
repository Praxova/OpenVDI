"""auth_tokens ORM model.

Refresh-token persistence for M4 JWT authentication. One row per
issued refresh token, identified by `id` (which becomes the JWT's
`jti` claim and the cookie's id half). The plaintext refresh secret
is never stored — only `bcrypt(secret_str)` lands in `refresh_hash`.
See M4-03 prompt and decisions A1, A8.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AuthToken(Base):
    """One row per issued refresh token. Primary key is the same UUID
    that ends up in the access token's `jti` claim and in the refresh
    cookie's id half (the cookie value is `<id>.<secret_str>`).

    The `username` column is denormalized (per A9) so admin tooling
    in M5+ can answer "revoke all tokens for user X" with one indexed
    query, without joining anything.
    """

    __tablename__ = "auth_tokens"
    __table_args__ = (
        Index("idx_auth_tokens_username", "username"),
        # Active-token lookup: per-user, unrevoked, expiry comparison.
        # The partial predicate is constant-foldable (revoked_at IS NULL),
        # which Postgres allows; an "expires_at > now()" predicate would
        # be rejected since now() is not IMMUTABLE.
        Index(
            "idx_auth_tokens_active",
            "username",
            "expires_at",
            postgresql_where="revoked_at IS NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    username: Mapped[str] = mapped_column(String(256), nullable=False)
    # bcrypt(refresh_secret_str) — never the plaintext.
    refresh_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    # Non-null when explicitly revoked (logout, admin tooling later).
    # Active tokens have this null; the partial index filters on it.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
