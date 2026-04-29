"""Unit tests for cookie format / parse helpers.

No DB, no HTTP, no fixtures beyond pytest itself. The cookie shape
is part of the wire contract with the portal (M4-16); regression
here would silently break the auth flow.
"""
from __future__ import annotations

from uuid import UUID, uuid4

from app.api.auth import (
    make_refresh_cookie_value,
    parse_refresh_cookie_value,
)


def test_make_and_parse_round_trip():
    token_id = uuid4()
    secret = "abc-DEF_123-XYZ_456"
    value = make_refresh_cookie_value(token_id, secret)
    parsed = parse_refresh_cookie_value(value)
    assert parsed is not None
    assert parsed == (token_id, secret)


def test_parse_returns_none_on_empty_string():
    assert parse_refresh_cookie_value("") is None


def test_parse_returns_none_on_no_dot():
    assert parse_refresh_cookie_value("just-an-id") is None


def test_parse_returns_none_on_dot_with_no_secret():
    assert parse_refresh_cookie_value(f"{uuid4()}.") is None


def test_parse_returns_none_on_invalid_uuid():
    assert parse_refresh_cookie_value("not-a-uuid.secret") is None


def test_parse_handles_dot_in_secret():
    """The secret is urlsafe-base64 (no dots produced by
    issue_refresh_token), but if a future format change introduced
    dots, partition() would still split on the first dot only.
    Pin that behavior here so the contract is explicit.
    """
    token_id = uuid4()
    weird_secret = "has.dots.in.it"
    value = make_refresh_cookie_value(token_id, weird_secret)
    parsed = parse_refresh_cookie_value(value)
    assert parsed is not None
    assert parsed == (token_id, weird_secret)


def test_make_uses_dot_separator():
    """Pin the wire format — `<uuid>.<secret>` with a single dot."""
    token_id = UUID("00000000-0000-0000-0000-000000000001")
    value = make_refresh_cookie_value(token_id, "S")
    assert value == "00000000-0000-0000-0000-000000000001.S"
