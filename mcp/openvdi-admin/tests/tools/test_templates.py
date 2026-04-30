"""Template tool tests."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openvdi_admin.errors import BrokerError
from openvdi_admin.tools import templates


@pytest.fixture
def mock_client(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(
        "openvdi_admin.tools._common.get_broker_client",
        lambda: client,
    )
    monkeypatch.setattr(
        "openvdi_admin.tools.templates.get_broker_client",
        lambda: client,
    )
    return client


@pytest.fixture
def writable(monkeypatch, settings):
    monkeypatch.setattr(
        "openvdi_admin.tools._common.get_settings",
        lambda: settings,
    )


class TestListTemplates:
    async def test_default_pagination(self, mock_client):
        mock_client.get.return_value = []
        await templates.openvdi_list_templates()
        params = mock_client.get.call_args.kwargs["params"]
        assert params["limit"] == 50
        assert params["offset"] == 0
        assert "cluster_id" not in params
        assert "os_type" not in params
        assert "status" not in params

    async def test_filters_passed_through(self, mock_client):
        mock_client.get.return_value = []
        await templates.openvdi_list_templates(
            cluster_id="c1",
            os_type="windows11",
            status="active",
            limit=20,
            offset=40,
        )
        params = mock_client.get.call_args.kwargs["params"]
        assert params["cluster_id"] == "c1"
        assert params["os_type"] == "windows11"
        assert params["status"] == "active"
        assert params["limit"] == 20
        assert params["offset"] == 40


class TestGetTemplate:
    async def test_uses_path_param(self, mock_client):
        mock_client.get.return_value = {"id": "t1"}
        result = await templates.openvdi_get_template("t1")
        assert result["id"] == "t1"
        mock_client.get.assert_called_once_with("/api/v1/templates/t1")


class TestRegisterTemplate:
    async def test_basic_register(self, mock_client, writable):
        mock_client.post.return_value = {"id": "new-t", "name": "win11"}
        await templates.openvdi_register_template(
            cluster_id="c1",
            name="win11",
            pve_vmid=9001,
            pve_node="pve1",
            os_type="windows11",
        )
        call = mock_client.post.call_args
        assert call.args[0] == "/api/v1/templates"
        body = call.kwargs["body"]
        assert body["cluster_id"] == "c1"
        assert body["pve_vmid"] == 9001
        assert body["cpu_cores"] == 2  # default
        assert body["memory_mb"] == 4096  # default
        assert body["disk_gb"] == 60  # default
        assert body["gpu_required"] is False  # default
        assert "description" not in body  # not passed

    async def test_with_description_and_overrides(
        self, mock_client, writable,
    ):
        mock_client.post.return_value = {"id": "new-t"}
        await templates.openvdi_register_template(
            cluster_id="c1",
            name="dev-vm",
            pve_vmid=9100,
            pve_node="pve2",
            os_type="ubuntu24",
            cpu_cores=8,
            memory_mb=16384,
            disk_gb=200,
            description="Heavy dev workstation",
            gpu_required=True,
        )
        body = mock_client.post.call_args.kwargs["body"]
        assert body["cpu_cores"] == 8
        assert body["memory_mb"] == 16384
        assert body["disk_gb"] == 200
        assert body["description"] == "Heavy dev workstation"
        assert body["gpu_required"] is True

    async def test_blocked_in_read_only_mode(
        self, mock_client, monkeypatch, settings,
    ):
        ro = settings.model_copy(update={"openvdi_mcp_read_only": True})
        monkeypatch.setattr(
            "openvdi_admin.tools._common.get_settings", lambda: ro,
        )
        with pytest.raises(BrokerError) as exc:
            await templates.openvdi_register_template(
                cluster_id="c1",
                name="x",
                pve_vmid=1,
                pve_node="p",
                os_type="windows11",
            )
        assert exc.value.code == "READ_ONLY_MODE"


class TestUpdateTemplate:
    async def test_only_passed_fields_in_body(
        self, mock_client, writable,
    ):
        mock_client.put.return_value = {"id": "t1"}
        await templates.openvdi_update_template(
            template_id="t1",
            name="renamed",
            cpu_cores=4,
        )
        body = mock_client.put.call_args.kwargs["body"]
        assert body == {"name": "renamed", "cpu_cores": 4}

    async def test_no_fields_sends_empty_body(
        self, mock_client, writable,
    ):
        mock_client.put.return_value = {"id": "t1"}
        await templates.openvdi_update_template(template_id="t1")
        body = mock_client.put.call_args.kwargs["body"]
        assert body == {}


class TestValidateTemplate:
    async def test_posts_to_validate_endpoint(self, mock_client, writable):
        mock_client.post.return_value = {"id": "t1", "status": "active"}
        result = await templates.openvdi_validate_template("t1")
        assert result["status"] == "active"
        mock_client.post.assert_called_once_with(
            "/api/v1/templates/t1/validate",
        )

    async def test_blocked_in_read_only_mode(
        self, mock_client, monkeypatch, settings,
    ):
        ro = settings.model_copy(update={"openvdi_mcp_read_only": True})
        monkeypatch.setattr(
            "openvdi_admin.tools._common.get_settings", lambda: ro,
        )
        with pytest.raises(BrokerError) as exc:
            await templates.openvdi_validate_template("t1")
        assert exc.value.code == "READ_ONLY_MODE"


class TestRetireTemplate:
    async def test_dry_run_with_blocking_pools(
        self, mock_client, writable,
    ):
        mock_client.get.side_effect = [
            {"id": "t1", "name": "win11"},
            [{"id": "p1", "name": "eng"}],
        ]
        result = await templates.openvdi_retire_template(
            template_id="t1", confirm=False,
        )
        assert result["dry_run"] is True
        assert result["action"] == "retire_template"
        assert result["target"] == {"id": "t1", "name": "win11"}
        assert result["blocked_by"]["pools"] == [
            {"id": "p1", "name": "eng"},
        ]
        mock_client.delete.assert_not_called()

    async def test_dry_run_no_blocking_pools(
        self, mock_client, writable,
    ):
        mock_client.get.side_effect = [
            {"id": "t1", "name": "win11"},
            [],
        ]
        result = await templates.openvdi_retire_template(
            template_id="t1", confirm=False,
        )
        assert result["blocked_by"] is None

    async def test_confirm_executes_delete(self, mock_client, writable):
        mock_client.delete.return_value = None
        result = await templates.openvdi_retire_template(
            template_id="t1", confirm=True,
        )
        assert result is None
        mock_client.delete.assert_called_once_with("/api/v1/templates/t1")
        mock_client.get.assert_not_called()
