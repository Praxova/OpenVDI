"""Tests for tools/_common.py — require_writable + dry_run_envelope."""
from __future__ import annotations

import pytest

from openvdi_admin.errors import BrokerError
from openvdi_admin.tools._common import (
    dry_run_envelope,
    require_writable,
)


class TestRequireWritable:
    def test_passes_when_not_read_only(self, monkeypatch, settings):
        # settings fixture has openvdi_mcp_read_only=False by default.
        monkeypatch.setattr(
            "openvdi_admin.tools._common.get_settings",
            lambda: settings,
        )
        # Should not raise.
        require_writable("openvdi_test_tool")

    def test_raises_when_read_only(self, monkeypatch, settings):
        ro_settings = settings.model_copy(
            update={"openvdi_mcp_read_only": True},
        )
        monkeypatch.setattr(
            "openvdi_admin.tools._common.get_settings",
            lambda: ro_settings,
        )
        with pytest.raises(BrokerError) as exc:
            require_writable("openvdi_destructive_thing")
        assert exc.value.code == "READ_ONLY_MODE"
        assert exc.value.http_status == 403
        assert "openvdi_destructive_thing" in exc.value.message


class TestDryRunEnvelope:
    def test_basic_shape(self):
        result = dry_run_envelope(
            action="delete_thing",
            target={"id": "abc", "name": "thing-1"},
        )
        assert result["ok"] is True
        assert result["dry_run"] is True
        assert result["action"] == "delete_thing"
        assert result["target"] == {"id": "abc", "name": "thing-1"}
        assert result["blocked_by"] is None
        assert result["note"] == ""

    def test_blocked_by_passes_through(self):
        result = dry_run_envelope(
            action="delete_cluster",
            target={"id": "c1"},
            blocked_by={"pools": [{"id": "p1", "name": "engineering"}]},
            note="confirm=True will fail",
        )
        assert result["blocked_by"] == {
            "pools": [{"id": "p1", "name": "engineering"}],
        }
        assert result["note"] == "confirm=True will fail"

    def test_extra_fields_merge(self):
        result = dry_run_envelope(
            action="x",
            target={},
            extra={"active_sessions": [{"id": "s1"}]},
        )
        assert result["active_sessions"] == [{"id": "s1"}]
        # Standard fields still present.
        assert result["dry_run"] is True
        assert result["action"] == "x"
