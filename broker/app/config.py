"""OpenVDI broker configuration via pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Proxmox VE ────────────────────────────────────────
    pve_api_url: str = "https://10.0.0.2:8006"
    pve_token_id: str = ""  # user@realm!tokenid
    pve_token_secret: str = ""  # uuid secret
    pve_verify_ssl: bool = False

    # ── Database ──────────────────────────────────────────
    db_url: str = "postgresql+asyncpg://openvdi:openvdi@localhost:5434/openvdi"

    # ── Broker ────────────────────────────────────────────
    broker_host: str = "0.0.0.0"
    broker_port: int = 8080
    log_level: str = "INFO"

    # ── Proxmox defaults ─────────────────────────────────
    default_node: str = "pis-dev"
    default_template_vmid: int = 9001
    clone_timeout: int = 300  # seconds
    task_poll_interval: float = 1.0


settings = Settings()
