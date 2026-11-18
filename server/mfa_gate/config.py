"""Настройки из окружения (префикс MFA_GATE_). Секреты — только из окружения или файлов *_FILE (Docker secrets), в БД и Git не хранятся."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MFA_GATE_", extra="ignore")

    database_url: str = "postgresql+asyncpg://mfa-gate:mfagate@postgres:5432/mfa-gate"
    master_key: str = ""              # base64 от 32 байт; шифрует TOTP-секреты, секреты RADIUS/LDAP, push-токены
    master_key_file: str = ""         # альтернатива master_key: путь к файлу с ключом
    public_url: str = "http://localhost:8080"   # адрес, который увидит мобильное приложение (в QR привязки)
    server_name: str = "MFA Gate"
    version: str = "0.2.0"

    # сколько ждать ответа на push; шлюзу нужен RADIUS-таймаут больше этого значения (рекомендуется >= 60 с)
    push_timeout: int = 45
    otp_challenge_timeout: int = 120
    radius_host: str = "0.0.0.0"  # noqa: S104
    radius_port: int = 1812
    radius_require_message_authenticator: bool = True

    enrollment_ttl_hours: int = 24
    admin_session_minutes: int = 30
    cookie_secure: str = "auto"   # auto — Secure, если запрос пришёл по HTTPS (в том числе через reverse proxy); true | false
    trusted_proxies: str = "127.0.0.1/32,::1/128"   # X-Forwarded-For принимается только от них

    # блокировки после ошибок первого/второго фактора
    lockout_threshold: int = 8
    lockout_minutes: int = 15

    push_fcm_credentials_file: str = ""   # service account JSON для FCM HTTP v1; пусто — FCM выключен
    push_hms_app_id: str = ""
    push_hms_client_secret: str = ""
    push_unifiedpush_hosts: str = ""      # разрешённые хосты распределителей UnifiedPush, через запятую; пусто — выключено

    audit_retention_days: int = 365
    auth_request_retention_days: int = 180

    log_level: str = "INFO"
    docs_enabled: bool = True
    static_dir: str = str(Path(__file__).resolve().parent.parent / "web")

    @model_validator(mode="after")
    def _load_key_file(self) -> Settings:
        if not self.master_key and self.master_key_file:
            self.master_key = Path(self.master_key_file).read_text().strip()
        return self


@lru_cache
def settings() -> Settings:
    return Settings()
