"""Settings for the openvdi-admin MCP server.

Loaded from environment variables (and optionally a .env file in
the working directory). The MCP server refuses to start if any
required variable is unset — fast-fail beats opaque-tool-failure.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration. See .env.example for the full set."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    openvdi_broker_url: HttpUrl = Field(
        ...,
        description=(
            "Broker base URL including scheme. Should be the same origin "
            "the portal uses (per docs/deploy.md → Same-Origin Requirement)."
        ),
    )
    openvdi_service_user: str = Field(
        ...,
        min_length=1,
        description="AD username of the MCP service account.",
    )
    openvdi_service_password: SecretStr = Field(
        ...,
        description=(
            "Service-account password. Loaded as SecretStr so it can't "
            "accidentally leak via repr()."
        ),
    )
    openvdi_verify_ssl: bool = Field(
        default=True,
        description=(
            "Verify the broker's TLS certificate. Set False only for "
            "self-signed dev clusters."
        ),
    )
    openvdi_mcp_read_only: bool = Field(
        default=False,
        description="When True, destructive tools refuse to execute (per S1).",
    )
    openvdi_mcp_log_format: str = Field(
        default="text",
        pattern="^(text|json)$",
        description="Log output format.",
    )
    openvdi_mcp_log_level: str = Field(
        default="INFO",
        description=(
            "Python logging level. Standard names: DEBUG, INFO, WARNING, ERROR."
        ),
    )
    openvdi_mcp_log_tool_starts: bool = Field(
        default=False,
        description=(
            "If True, emit a 'tool started' log line in addition to "
            "the 'tool completed' line. Doubles log volume. Useful "
            "when debugging hangs (start log proves the tool entered "
            "at all). Default False."
        ),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. The cache means env-var changes after
    process start are NOT picked up — restart the MCP for config changes
    (per A6: no credential rotation in v0)."""
    return Settings()  # type: ignore[call-arg]
