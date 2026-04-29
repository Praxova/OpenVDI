"""auth_tokens

M4 refresh-token persistence. See M4-03 prompt and the AuthToken
model docstring for the full design (one row per issued refresh
token, denormalized username, bcrypt hash never plaintext).

Revision ID: 0002_auth_tokens
Revises: 0001_baseline_m3
Create Date: 2026-04-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "0002_auth_tokens"
down_revision = "0001_baseline_m3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_tokens",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("username", sa.String(256), nullable=False),
        sa.Column("refresh_hash", sa.LargeBinary, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_auth_tokens_username", "auth_tokens", ["username"],
    )
    op.create_index(
        "idx_auth_tokens_active",
        "auth_tokens",
        ["username", "expires_at"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_auth_tokens_active", table_name="auth_tokens",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_index("idx_auth_tokens_username", table_name="auth_tokens")
    op.drop_table("auth_tokens")
