"""OpenVDI broker configuration.

Single Settings class loaded from the repo-root .env file. Proxmox-flavored
fields for Milestone 1; when a second provider arrives, config will be
restructured.
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

from pydantic import SecretStr, field_validator, model_validator
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

    # ── M4: Auth mode ───────────────────────────────────────────
    # 'jwt' is production. 'dev' retains the M2/M3 X-Dev-* header path
    # for local development. Default is 'jwt' so production deployments
    # don't accidentally ship dev-mode auth (per F2).
    openvdi_auth_mode: Literal["jwt", "dev"] = "jwt"

    # ── M4: JWT signing key (jwt mode only) ─────────────────────
    # HS256 signing key. Required in jwt mode; ignored in dev mode.
    # Must be ≥32 bytes (per F3). The model validator below enforces
    # both presence and length when auth_mode='jwt'. SecretStr("") as
    # the default lets the field be unset in dev mode.
    openvdi_jwt_secret: SecretStr = SecretStr("")

    # ── M4: LDAP (jwt mode only) ────────────────────────────────
    openvdi_ldap_url: str = ""
    openvdi_ldap_bind_dn: str = ""
    openvdi_ldap_bind_password: SecretStr = SecretStr("")
    openvdi_ldap_user_base: str = ""
    openvdi_ldap_group_base: str = ""

    # Filter templates with {username} / {user_dn} placeholders.
    # The LDAP service substitutes after escape_filter_chars() to
    # prevent LDAP-injection on user-provided usernames. Defaults
    # match Active Directory; pure-LDAP installs override.
    openvdi_ldap_user_filter: str = "(sAMAccountName={username})"
    openvdi_ldap_group_filter: str = "(member={user_dn})"

    # Group whose members are granted role=admin. Compared by name (CN);
    # case-insensitive match.
    openvdi_ldap_admin_group: str = ""

    # TLS verification for ldaps://. Default true; set false only for
    # self-signed CAs in dev/test.
    openvdi_ldap_verify_ssl: bool = True

    # ── M4: Portal origin (jwt mode only, per A10) ──────────────
    # Single origin where the portal is served from in production
    # (e.g. https://openvdi.example.com). Required for SameSite=Strict
    # refresh-cookie semantics. See docs/deploy.md → Same-Origin
    # Requirement.
    openvdi_portal_origin: str = ""

    # ── M4: Logging ─────────────────────────────────────────────
    # 'text' is dev-friendly; 'json' is production-recommended. The
    # JSON formatter swap lands in M4-12; this field exists now so
    # M4-12 doesn't have to thread it in then.
    openvdi_log_format: Literal["text", "json"] = "text"
    openvdi_log_level: str = "INFO"
    # httpx is chatty on DEBUG; this knob already exists in main.py
    # as a direct os.environ read (M2). M4-12 hoists the actual
    # consumer into _configure_logging(); for now, the field exists
    # so callers know to use it.
    openvdi_log_level_httpx: str = "WARNING"

    # ── M4: Audit retention ─────────────────────────────────────
    # The audit_retention worker (M4-13) prunes audit_log rows older
    # than this. 90 is a developer guess; operators tune via env per
    # their compliance needs.
    openvdi_audit_retention_days: int = 90

    @property
    def is_dev_auth(self) -> bool:
        return self.openvdi_auth_mode == "dev"

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

    @model_validator(mode="after")
    def _validate_auth_mode_requirements(self) -> "Settings":
        """Enforce the jwt-mode-required field set + JWT secret length.

        Runs after every field is populated (model-level validator), so
        we can branch on auth_mode. In dev mode this is a no-op; in jwt
        mode every M4-required field must be set and the JWT secret
        must be ≥32 bytes (per F3 — short keys are trivially brute-forced).
        """
        if self.openvdi_auth_mode != "jwt":
            return self
        missing: list[str] = []
        if not self.openvdi_jwt_secret.get_secret_value():
            missing.append("OPENVDI_JWT_SECRET")
        if not self.openvdi_ldap_url:
            missing.append("OPENVDI_LDAP_URL")
        if not self.openvdi_ldap_bind_dn:
            missing.append("OPENVDI_LDAP_BIND_DN")
        if not self.openvdi_ldap_bind_password.get_secret_value():
            missing.append("OPENVDI_LDAP_BIND_PASSWORD")
        if not self.openvdi_ldap_user_base:
            missing.append("OPENVDI_LDAP_USER_BASE")
        if not self.openvdi_ldap_group_base:
            missing.append("OPENVDI_LDAP_GROUP_BASE")
        if not self.openvdi_ldap_admin_group:
            missing.append("OPENVDI_LDAP_ADMIN_GROUP")
        if not self.openvdi_portal_origin:
            missing.append("OPENVDI_PORTAL_ORIGIN")
        if missing:
            raise ValueError(
                "OPENVDI_AUTH_MODE=jwt requires the following env vars: "
                + ", ".join(missing)
            )
        secret_bytes = len(
            self.openvdi_jwt_secret.get_secret_value().encode("utf-8")
        )
        if secret_bytes < 32:
            raise ValueError(
                f"OPENVDI_JWT_SECRET must be at least 32 bytes "
                f"(got {secret_bytes}). Generate one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return self


@functools.lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance."""
    return Settings()
