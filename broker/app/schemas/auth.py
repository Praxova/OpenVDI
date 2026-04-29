"""Pydantic schemas for the /auth/* endpoints.

LoginRequest is the body of POST /auth/login. Refresh and logout
take no request body — they read from the refresh_token cookie.

TokenResponse is the body shape returned (wrapped in APIResponse[T])
on successful login or refresh. Its `expires_in` is the access
token's TTL in seconds — matches OAuth 2.0 conventions for the
field name and units.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    # max_length 256 matches auth_tokens.username and
    # entitlements.principal_name column widths. Inputs longer are
    # rejected at the schema layer before LDAP sees them.
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=512)


class TokenResponse(BaseModel):
    access_token: str
    expires_in: int  # seconds — equal to ACCESS_TOKEN_TTL_SECONDS
    role: Literal["admin", "user"]
