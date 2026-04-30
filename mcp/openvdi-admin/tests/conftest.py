"""Pytest fixtures + httpx-mock setup."""
from __future__ import annotations

import pytest
import respx
from pydantic import SecretStr

from openvdi_admin.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Default settings for tests. Override individual fields per-test
    if needed by reconstructing or using model_copy()."""
    return Settings(
        openvdi_broker_url="https://broker.test",  # type: ignore[arg-type]
        openvdi_service_user="mcp-svc",
        openvdi_service_password=SecretStr("test-password"),
        openvdi_verify_ssl=False,
        openvdi_mcp_read_only=False,
        openvdi_mcp_log_format="text",
        openvdi_mcp_log_level="DEBUG",
    )


@pytest.fixture
def mock_broker():
    """Yields a respx.Router. Tests use it to declare expected HTTP
    exchanges against the broker base URL."""
    with respx.mock(base_url="https://broker.test") as router:
        yield router
