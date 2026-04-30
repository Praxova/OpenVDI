"""Entitlement tool tests."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openvdi_admin.errors import BrokerError
from openvdi_admin.tools import entitlements


@pytest.fixture
def mock_client(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(
        "openvdi_admin.tools._common.get_broker_client",
        lambda: client,
    )
    monkeypatch.setattr(
        "openvdi_admin.tools.entitlements.get_broker_client",
        lambda: client,
    )
    return client


@pytest.fixture
def writable(monkeypatch, settings):
    monkeypatch.setattr(
        "openvdi_admin.tools._common.get_settings",
        lambda: settings,
    )


class TestListEntitlements:
    async def test_no_filter(self, mock_client):
        mock_client.get.return_value = []
        await entitlements.openvdi_list_entitlements(pool_id="p1")
        call = mock_client.get.call_args
        assert call.args[0] == "/api/v1/pools/p1/entitlements"
        assert call.kwargs["params"] == {}

    async def test_principal_type_filter(self, mock_client):
        mock_client.get.return_value = []
        await entitlements.openvdi_list_entitlements(
            pool_id="p1", principal_type="group",
        )
        params = mock_client.get.call_args.kwargs["params"]
        assert params["principal_type"] == "group"


class TestGrantEntitlement:
    async def test_basic_grant(self, mock_client, writable):
        mock_client.post.return_value = {
            "id": "e1",
            "principal_type": "group",
            "principal_name": "VDI-Engineering",
        }
        result = await entitlements.openvdi_grant_entitlement(
            pool_id="p1",
            principal_type="group",
            principal_name="VDI-Engineering",
        )
        assert result["id"] == "e1"
        call = mock_client.post.call_args
        assert call.args[0] == "/api/v1/pools/p1/entitlements"
        body = call.kwargs["body"]
        assert body == {
            "principal_type": "group",
            "principal_name": "VDI-Engineering",
        }

    async def test_blocked_in_read_only_mode(
        self, mock_client, monkeypatch, settings,
    ):
        ro = settings.model_copy(update={"openvdi_mcp_read_only": True})
        monkeypatch.setattr(
            "openvdi_admin.tools._common.get_settings", lambda: ro,
        )
        with pytest.raises(BrokerError) as exc:
            await entitlements.openvdi_grant_entitlement(
                pool_id="p1",
                principal_type="group",
                principal_name="VDI-Engineering",
            )
        assert exc.value.code == "READ_ONLY_MODE"


class TestRevokeEntitlement:
    async def test_dry_run_user_with_active_sessions(
        self, mock_client, writable,
    ):
        mock_client.get.side_effect = [
            [
                {
                    "id": "e1",
                    "principal_type": "user",
                    "principal_name": "alice",
                },
            ],
            [{"id": "s1", "username": "alice"}],
        ]
        result = await entitlements.openvdi_revoke_entitlement(
            pool_id="p1", entitlement_id="e1", confirm=False,
        )
        assert result["dry_run"] is True
        assert result["action"] == "revoke_entitlement"
        assert result["target"]["principal_name"] == "alice"
        assert result["blocked_by"] is None
        assert result["active_sessions"] == [
            {"id": "s1", "username": "alice"},
        ]
        mock_client.delete.assert_not_called()
        # second .get must be the sessions lookup with username/pool/status
        sessions_call = mock_client.get.call_args_list[1]
        assert sessions_call.args[0] == "/api/v1/sessions"
        assert sessions_call.kwargs["params"] == {
            "username": "alice",
            "pool_id": "p1",
            "status": "active",
        }

    async def test_dry_run_group_skips_session_lookup(
        self, mock_client, writable,
    ):
        mock_client.get.side_effect = [
            [
                {
                    "id": "e1",
                    "principal_type": "group",
                    "principal_name": "VDI-Engineering",
                },
            ],
        ]
        result = await entitlements.openvdi_revoke_entitlement(
            pool_id="p1", entitlement_id="e1", confirm=False,
        )
        assert result["active_sessions"] == []
        # only one GET — no /api/v1/sessions lookup for group principals
        assert mock_client.get.call_count == 1

    async def test_dry_run_nonexistent_raises_not_found(
        self, mock_client, writable,
    ):
        mock_client.get.return_value = [
            {
                "id": "other",
                "principal_type": "user",
                "principal_name": "bob",
            },
        ]
        with pytest.raises(BrokerError) as exc:
            await entitlements.openvdi_revoke_entitlement(
                pool_id="p1", entitlement_id="missing", confirm=False,
            )
        assert exc.value.code == "NOT_FOUND"
        assert exc.value.http_status == 404

    async def test_confirm_executes_delete(self, mock_client, writable):
        mock_client.delete.return_value = None
        result = await entitlements.openvdi_revoke_entitlement(
            pool_id="p1", entitlement_id="e1", confirm=True,
        )
        assert result is None
        mock_client.delete.assert_called_once_with(
            "/api/v1/pools/p1/entitlements/e1",
        )
        mock_client.get.assert_not_called()

    async def test_blocked_in_read_only_mode(
        self, mock_client, monkeypatch, settings,
    ):
        ro = settings.model_copy(update={"openvdi_mcp_read_only": True})
        monkeypatch.setattr(
            "openvdi_admin.tools._common.get_settings", lambda: ro,
        )
        with pytest.raises(BrokerError) as exc:
            await entitlements.openvdi_revoke_entitlement(
                pool_id="p1", entitlement_id="e1", confirm=False,
            )
        assert exc.value.code == "READ_ONLY_MODE"
