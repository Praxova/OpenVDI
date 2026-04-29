"""Pytest config + shared fixtures for broker tests."""
from __future__ import annotations

import pytest

from app.config import Settings


@pytest.fixture
def monkeypatch_settings(monkeypatch):
    """Construct a Settings instance with arbitrary env values.

    Returns a factory: pass env-var-name=value kwargs and the factory
    sets each via monkeypatch then constructs a fresh Settings(),
    bypassing the lru_cache on get_settings() so tests get isolation
    automatically. monkeypatch handles teardown.

    Use the env-var-name form (UPPER_SNAKE) — pydantic-settings is
    case-insensitive on lookup, so this matches what operators see.
    """
    def _make(**env_vars: str) -> Settings:
        for k, v in env_vars.items():
            monkeypatch.setenv(k, str(v))
        return Settings()

    return _make
