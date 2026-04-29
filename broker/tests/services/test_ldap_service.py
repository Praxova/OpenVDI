"""Unit tests for LDAPService.

Mocks ldap3.Connection at the import boundary inside ldap_service so
each test drives the service's branch logic without a real LDAP
server. The state machine has enough subtlety (filter escaping,
canonicalization, error mapping, three-bind sequence, admin
determination) that exercising it deterministically is worth the
mock setup.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from ldap3.core.exceptions import (
    LDAPBindError,
    LDAPInvalidCredentialsResult,
)

from app.services.ldap_service import (
    LDAPAuthError,
    LDAPAuthResult,
    LDAPService,
    LDAPServiceError,
)


# ── Shared fixtures ──────────────────────────────────────────


@pytest.fixture
def ldap_settings(monkeypatch_settings):
    """A Settings instance with auth_mode=jwt + plausible LDAP fields."""
    return monkeypatch_settings(
        OPENVDI_AUTH_MODE="jwt",
        OPENVDI_JWT_SECRET="x" * 64,
        OPENVDI_LDAP_URL="ldap://test.example.com",
        OPENVDI_LDAP_BIND_DN="CN=svc,DC=example,DC=com",
        OPENVDI_LDAP_BIND_PASSWORD="svc-pw",
        OPENVDI_LDAP_USER_BASE="OU=Users,DC=example,DC=com",
        OPENVDI_LDAP_GROUP_BASE="OU=Groups,DC=example,DC=com",
        OPENVDI_LDAP_ADMIN_GROUP="OpenVDI-Admins",
        OPENVDI_PORTAL_ORIGIN="https://test.example.com",
    )


class _FakeConnection:
    """Stand-in for ldap3.Connection that supports the service's
    `with conn:` + `conn.search()` + `conn.entries` surface.
    """

    def __init__(
        self, entries: list, ctor_record: dict,
    ) -> None:
        self.entries = entries
        self._ctor_record = ctor_record

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *args) -> None:
        return None

    def search(self, **kwargs) -> None:
        # Capture the filter so tests can assert on injection escape.
        self._ctor_record["search_filter"] = kwargs.get("search_filter")
        self._ctor_record["search_base"] = kwargs.get("search_base")


class _ConnectionMocker:
    """Replaces ldap3.Connection in ldap_service. Tests register a
    sequence of "what each construction returns" via queue(). Each
    call to the fake pops the next step.
    """

    def __init__(self) -> None:
        self.constructions: list[dict] = []
        self.steps: list[dict] = []

    def queue(self, *steps: dict) -> None:
        self.steps = list(steps)

    def __call__(self, server, user=None, password=None, **kwargs):
        record = {
            "user": user,
            "password": password,
            "kwargs": kwargs,
        }
        self.constructions.append(record)
        if not self.steps:
            raise AssertionError(
                "No more queued Connection steps "
                f"(construction #{len(self.constructions)})"
            )
        step = self.steps.pop(0)
        if "raises" in step:
            raise step["raises"]
        return _FakeConnection(step.get("entries", []), record)


@pytest.fixture
def ldap_mocker(monkeypatch):
    state = _ConnectionMocker()
    import app.services.ldap_service as mod
    monkeypatch.setattr(mod, "Connection", state)
    return state


def _entry(dn: str | None = None, cn: str | None = None) -> MagicMock:
    """Build a fake ldap3 Entry with optional entry_dn and cn.value."""
    e = MagicMock(spec=[])  # spec=[] so hasattr() is honest
    if dn is not None:
        e.entry_dn = dn
    if cn is not None:
        e.cn = MagicMock()
        e.cn.value = cn
    return e


# ── Tests ────────────────────────────────────────────────────


async def test_happy_path_admin_user(ldap_settings, ldap_mocker):
    """alice exists, password matches, member of OpenVDI-Admins."""
    ldap_mocker.queue(
        # 1. service-account search returns alice's entry
        {"entries": [_entry(dn="CN=Alice,OU=Users,DC=example,DC=com")]},
        # 2. user bind succeeds (no entries needed)
        {"entries": []},
        # 3. group search returns Engineering + OpenVDI-Admins
        {"entries": [
            _entry(cn="Engineering"),
            _entry(cn="OpenVDI-Admins"),
        ]},
    )

    svc = LDAPService(ldap_settings)
    result = await svc.authenticate("alice", "good-password")

    assert isinstance(result, LDAPAuthResult)
    assert result.username == "alice"
    assert result.user_dn == "CN=Alice,OU=Users,DC=example,DC=com"
    assert "OpenVDI-Admins" in result.groups
    assert result.is_admin is True
    # Three Connection constructions: service-search, user-bind,
    # service-group-search.
    assert len(ldap_mocker.constructions) == 3


async def test_user_not_found_returns_auth_error(
    ldap_settings, ldap_mocker,
):
    """Service-account search returns 0 entries → invalid credentials."""
    ldap_mocker.queue(
        {"entries": []},  # search returns nothing
    )
    svc = LDAPService(ldap_settings)
    with pytest.raises(LDAPAuthError):
        await svc.authenticate("ghost", "any-password")
    # Only the service-search Connection was opened — no user-bind.
    assert len(ldap_mocker.constructions) == 1


async def test_bad_password_returns_auth_error(
    ldap_settings, ldap_mocker,
):
    """User found, but the user-bind raises LDAPInvalidCredentialsResult."""
    ldap_mocker.queue(
        {"entries": [_entry(dn="CN=Alice,OU=Users,DC=example,DC=com")]},
        {"raises": LDAPInvalidCredentialsResult("bad password")},
    )
    svc = LDAPService(ldap_settings)
    with pytest.raises(LDAPAuthError):
        await svc.authenticate("alice", "wrong-password")
    # Service-search + user-bind opened; group-search never happens.
    assert len(ldap_mocker.constructions) == 2


async def test_service_account_bind_failure_is_service_error(
    ldap_settings, ldap_mocker,
):
    """First Connection raises LDAPBindError → LDAPServiceError."""
    ldap_mocker.queue(
        {"raises": LDAPBindError("svc account locked")},
    )
    svc = LDAPService(ldap_settings)
    with pytest.raises(LDAPServiceError):
        await svc.authenticate("alice", "any-password")


async def test_username_canonicalized_lowercase(
    ldap_settings, ldap_mocker,
):
    """Input 'Alice' → result.username='alice' AND filter sees lowercase."""
    ldap_mocker.queue(
        {"entries": [_entry(dn="CN=Alice,OU=Users,DC=example,DC=com")]},
        {"entries": []},
        {"entries": []},
    )
    svc = LDAPService(ldap_settings)
    result = await svc.authenticate("Alice", "pw")
    assert result.username == "alice"
    # The search filter on construction #1 substituted lowercase.
    search_filter = ldap_mocker.constructions[0]["search_filter"]
    assert "alice" in search_filter
    assert "Alice" not in search_filter


async def test_filter_escaping_for_injection_safety(
    ldap_settings, ldap_mocker,
):
    """Username 'alice)(cn=*)' is escaped before filter substitution."""
    ldap_mocker.queue(
        {"entries": []},  # nothing matches the escaped filter — OK
    )
    svc = LDAPService(ldap_settings)
    with pytest.raises(LDAPAuthError):
        await svc.authenticate("alice)(cn=*)", "pw")
    search_filter = ldap_mocker.constructions[0]["search_filter"]
    # ldap3.utils.conv.escape_filter_chars renders '(' as \28 and ')' as \29.
    assert "\\28" in search_filter or r"\28" in search_filter
    assert "\\29" in search_filter or r"\29" in search_filter


async def test_empty_password_rejected_without_ldap_call(
    ldap_settings, ldap_mocker,
):
    """Empty password → LDAPAuthError without any Connection construction."""
    svc = LDAPService(ldap_settings)
    with pytest.raises(LDAPAuthError):
        await svc.authenticate("alice", "")
    assert len(ldap_mocker.constructions) == 0


async def test_is_admin_false_when_user_not_in_admin_group(
    ldap_settings, ldap_mocker,
):
    """Group search returns membership but no admin group."""
    ldap_mocker.queue(
        {"entries": [_entry(dn="CN=Bob,OU=Users,DC=example,DC=com")]},
        {"entries": []},
        {"entries": [_entry(cn="Engineering")]},  # not admin
    )
    svc = LDAPService(ldap_settings)
    result = await svc.authenticate("bob", "pw")
    assert result.is_admin is False
    assert result.groups == ("Engineering",)


async def test_is_admin_case_insensitive_match(
    ldap_settings, ldap_mocker,
):
    """Admin group config 'OpenVDI-Admins' matches LDAP 'openvdi-admins'."""
    ldap_mocker.queue(
        {"entries": [_entry(dn="CN=Carol,OU=Users,DC=example,DC=com")]},
        {"entries": []},
        {"entries": [_entry(cn="openvdi-admins")]},  # lowercase variant
    )
    svc = LDAPService(ldap_settings)
    result = await svc.authenticate("carol", "pw")
    assert result.is_admin is True


async def test_multiple_user_matches_is_service_error(
    ldap_settings, ldap_mocker,
):
    """Search returns 2 entries → LDAPServiceError, not AuthError.

    Treating ambiguity as auth-failure would mask a directory
    misconfiguration that warrants an operator alert.
    """
    ldap_mocker.queue(
        {"entries": [
            _entry(dn="CN=Alice,OU=Users,DC=example,DC=com"),
            _entry(dn="CN=Alice,OU=Contractors,DC=example,DC=com"),
        ]},
    )
    svc = LDAPService(ldap_settings)
    with pytest.raises(LDAPServiceError):
        await svc.authenticate("alice", "pw")
