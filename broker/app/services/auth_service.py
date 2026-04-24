"""M2 header-pretend auth + forward-compatible User shape.

The `User` class lands on `request.state.user` from the DevAuth
middleware today; in M4 the same class will be populated from JWT
claims. Keeping the shape stable across the pre-JWT / post-JWT cut
makes the M4 upgrade additive — consumers (require_admin, /me/*
handlers, the broker service) don't change.

Scope for M2: header → User. No LDAP, no JWT, no DB lookups, no
session state, no users table (W-8).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, HTTPException, Request


Role = Literal["admin", "user"]


@dataclass(frozen=True, slots=True)
class User:
    """Authenticated principal. Immutable and hashable.

    `groups` is a tuple (not a list) so the dataclass stays hashable —
    useful for any future memoization keyed on user identity. Ordering
    is preserved as the caller supplied it.
    """

    username: str
    groups: tuple[str, ...]
    role: Role

    def is_admin(self) -> bool:
        return self.role == "admin"


def parse_groups_header(value: str | None) -> tuple[str, ...]:
    """Parse X-Dev-Groups: comma-separated, whitespace-stripped, empty removed.

    Returns an empty tuple for None or empty string.
    """
    if not value:
        return ()
    return tuple(g for g in (part.strip() for part in value.split(",")) if g)


def parse_role_header(value: str | None) -> Role:
    """Parse X-Dev-Role.

    Defaults to "user" for None or empty. Case-sensitive: "ADMIN" and
    other variants raise ValueError — matches the future JWT claim
    pattern where role values are canonical strings, not human-typed.
    """
    if value is None or value == "":
        return "user"
    if value not in ("admin", "user"):
        raise ValueError(
            f"X-Dev-Role must be 'admin' or 'user' (got {value!r})"
        )
    return value  # type: ignore[return-value]


# ── FastAPI dependencies ──────────────────────────────────────


def current_user(request: Request) -> User:
    """Retrieve the authenticated user attached by DevAuthMiddleware.

    Never None — the middleware short-circuits unauthenticated requests
    before they reach any handler declaring this dependency.
    """
    return request.state.user


def require_admin(user: User = Depends(current_user)) -> User:
    """403 if the user is not an admin."""
    if not user.is_admin():
        raise HTTPException(
            status_code=403,
            detail={"code": "FORBIDDEN", "message": "admin role required"},
        )
    return user


def require_user(user: User = Depends(current_user)) -> User:
    """No-op gate; declared explicitly on /me/* handlers for readability.

    Makes the "this endpoint requires authentication but no special
    role" contract visible in the function signature.
    """
    return user
