"""OpenVDI broker configuration.

Single Settings class loaded from the repo-root .env file. Proxmox-flavored
fields for Milestone 1; when a second provider arrives, config will be
restructured.
"""
from __future__ import annotations

import functools
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env relative to this file so the path works regardless of cwd.
# broker/app/config.py -> broker/app -> broker -> repo root
_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

    proxmox_api_url: str
    proxmox_token_id: str
    proxmox_token_secret: SecretStr
    proxmox_verify_ssl: bool = True
    proxmox_default_node: str
    proxmox_template_vmid: int
    proxmox_test_vmid: int
    proxmox_target_storage: str | None = None

    # Application-layer secret encryption (Fernet). Required at startup;
    # missing key fails fast rather than silently producing garbage.
    openvdi_encryption_key: SecretStr

    # Postgres connection parameters. database_url assembles these into
    # the asyncpg DSN consumed by SQLAlchemy in broker/app/database.py.
    postgres_user: str
    postgres_password: SecretStr
    postgres_db: str
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    @property
    def database_url(self) -> str:
        pw = quote_plus(self.postgres_password.get_secret_value())
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{pw}@"
            f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @field_validator("proxmox_target_storage", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v):
        # .env often sets unused optional fields to "" rather than omitting
        # them. Treat empty string as unset so downstream code sees None.
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    @field_validator("openvdi_encryption_key", mode="after")
    @classmethod
    def _non_empty_encryption_key(cls, v: SecretStr) -> SecretStr:
        # Empty string would pass the SecretStr type check but is a
        # configuration bug — fail loudly at Settings() construction.
        if not v.get_secret_value():
            raise ValueError("OPENVDI_ENCRYPTION_KEY must not be empty")
        return v


@functools.lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance."""
    return Settings()
