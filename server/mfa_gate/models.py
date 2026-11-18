"""Модель данных. Пользователь один на все источники (local/ldap); административная роль — поле пользователя, а не отдельная учётная запись."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _id()
    login: Mapped[str] = mapped_column(String(255), unique=True, index=True)   # всегда в нижнем регистре
    display_name: Mapped[str] = mapped_column(String(255), default="")
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16), default="local")          # local | ldap
    directory_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ldap_directories.id", ondelete="SET NULL"))
    external_id: Mapped[str | None] = mapped_column(String(255), index=True)  # objectGUID / entryUUID
    password_hash: Mapped[str | None] = mapped_column(String(255))            # только для local
    status: Mapped[str] = mapped_column(String(16), default="active")         # active | disabled
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)   # временный пароль первого администратора
    admin_role: Mapped[str | None] = mapped_column(String(32))                # superadmin | admin | operator | auditor
    directory_groups: Mapped[list] = mapped_column(JSONB, default=list)       # снимок групп каталога с последнего входа
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()

    groups: Mapped[list[Group]] = relationship(secondary="user_groups", back_populates="users", lazy="selectin")


class Group(Base):
    __tablename__ = "groups"
    id: Mapped[uuid.UUID] = _id()
    name: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    # имя или DN группы каталога: участники каталога считаются членами этой группы без ручного добавления
    directory_group: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = _created()

    users: Mapped[list[User]] = relationship(secondary="user_groups", back_populates="groups", lazy="raise")


class UserGroup(Base):
    __tablename__ = "user_groups"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    platform: Mapped[str] = mapped_column(String(32), default="android")
    app_version: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")          # active | revoked
    secret_hash: Mapped[str] = mapped_column(String(64))                       # SHA-256(device_secret), hex
    public_key: Mapped[str] = mapped_column(Text)                              # SPKI DER, base64url
    push_provider: Mapped[str] = mapped_column(String(16), default="none")     # fcm | hms | unifiedpush | none
    push_token_enc: Mapped[str | None] = mapped_column(Text)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class TotpFactor(Base):
    __tablename__ = "totp_factors"
    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128), default="Аутентификатор")
    secret_enc: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")         # pending | active
    last_step: Mapped[int | None] = mapped_column(BigInteger)                  # защита от повтора принятого кода
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class OneTimeToken(Base):
    """Одноразовые токены: привязка устройства, аварийный вход администратора."""
    __tablename__ = "one_time_tokens"
    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(32))                           # enroll_device | emergency_admin
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(255), default="system")
    created_at: Mapped[datetime] = _created()


class Resource(Base):
    """Защищаемый ресурс: VPN-шлюз, приложение. К нему привязаны RADIUS- и API-клиенты, на него ссылаются политики."""
    __tablename__ = "resources"
    id: Mapped[uuid.UUID] = _id()
    name: Mapped[str] = mapped_column(String(255), unique=True)
    kind: Mapped[str] = mapped_column(String(32), default="vpn")               # vpn | app | other
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _created()


class RadiusClient(Base):
    __tablename__ = "radius_clients"
    id: Mapped[uuid.UUID] = _id()
    resource_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resources.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    address: Mapped[str] = mapped_column(String(64))                           # IP или CIDR источника запросов
    secret_enc: Mapped[str] = mapped_column(Text)
    nas_identifier: Mapped[str | None] = mapped_column(String(255))            # если задан — обязан совпасть
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class ApiClient(Base):
    __tablename__ = "api_clients"
    id: Mapped[uuid.UUID] = _id()
    name: Mapped[str] = mapped_column(String(255))
    key_id: Mapped[str] = mapped_column(String(32), unique=True)
    secret_hash: Mapped[str] = mapped_column(String(64))
    scopes: Mapped[list] = mapped_column(JSONB, default=list)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("resources.id", ondelete="SET NULL"))
    allowed_cidrs: Mapped[list] = mapped_column(JSONB, default=list)           # пусто — любой адрес
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class LdapDirectory(Base):
    __tablename__ = "ldap_directories"
    id: Mapped[uuid.UUID] = _id()
    name: Mapped[str] = mapped_column(String(255), unique=True)
    kind: Mapped[str] = mapped_column(String(16), default="ad")                # ad | openldap
    hosts: Mapped[list] = mapped_column(JSONB, default=list)                   # несколько контроллеров: перебор при недоступности
    port: Mapped[int] = mapped_column(Integer, default=636)
    encryption: Mapped[str] = mapped_column(String(16), default="ldaps")       # ldaps | starttls | none
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    ca_cert: Mapped[str | None] = mapped_column(Text)                          # PEM, если CA не из системного набора
    bind_dn: Mapped[str] = mapped_column(String(1024), default="")
    bind_password_enc: Mapped[str | None] = mapped_column(Text)
    base_dn: Mapped[str] = mapped_column(String(1024))
    user_filter: Mapped[str] = mapped_column(String(1024), default="(&(objectClass=user)(objectCategory=person))")
    login_attribute: Mapped[str] = mapped_column(String(64), default="sAMAccountName")
    id_attribute: Mapped[str] = mapped_column(String(64), default="objectGUID")
    name_attribute: Mapped[str] = mapped_column(String(64), default="displayName")
    email_attribute: Mapped[str] = mapped_column(String(64), default="mail")
    phone_attribute: Mapped[str] = mapped_column(String(64), default="mobile")
    nested_groups: Mapped[bool] = mapped_column(Boolean, default=True)
    allowed_groups: Mapped[list] = mapped_column(JSONB, default=list)          # пусто — вход разрешён всем найденным
    timeout: Mapped[int] = mapped_column(Integer, default=5)
    priority: Mapped[int] = mapped_column(Integer, default=10)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _created()


class Policy(Base):
    """Правила проверяются по возрастанию priority, применяется первое совпавшее. Нет совпадений — MFA обязателен (безопасное умолчание)."""
    __tablename__ = "policies"
    id: Mapped[uuid.UUID] = _id()
    name: Mapped[str] = mapped_column(String(255))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    resource_ids: Mapped[list] = mapped_column(JSONB, default=list)            # пусто — любой ресурс
    groups: Mapped[list] = mapped_column(JSONB, default=list)                  # пусто — любой пользователь
    exclude_groups: Mapped[list] = mapped_column(JSONB, default=list)
    action: Mapped[str] = mapped_column(String(16), default="mfa")             # mfa | allow | deny
    methods: Mapped[list] = mapped_column(JSONB, default=lambda: ["push", "totp"])
    # поведение при невозможности проверить второй фактор (нет push-канала, сбой доставки): deny (умолчание) | allow (журналируется)
    on_unavailable: Mapped[str] = mapped_column(String(16), default="deny")
    created_at: Mapped[datetime] = _created()


class AuthRequest(Base):
    """Транзакция аутентификации: одна на попытку входа, независимо от числа повторов RADIUS."""
    __tablename__ = "auth_requests"
    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    login: Mapped[str] = mapped_column(String(255), index=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("resources.id", ondelete="SET NULL"))
    resource_name: Mapped[str] = mapped_column(String(255), default="")
    channel: Mapped[str] = mapped_column(String(16))                           # radius | api | admin
    client_name: Mapped[str] = mapped_column(String(255), default="")
    api_client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("api_clients.id", ondelete="SET NULL"))   # результат видит только он
    source_ip: Mapped[str | None] = mapped_column(String(64))                  # адрес пользователя (Calling-Station-Id)
    nas_ip: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), index=True)                # pending | approved | denied | expired | rejected | allowed
    method: Mapped[str | None] = mapped_column(String(16))                     # push | totp | policy
    reason: Mapped[str | None] = mapped_column(String(64))
    challenge: Mapped[str | None] = mapped_column(String(64))
    session_ref: Mapped[str | None] = mapped_column(String(64))                # вход в панель: id сессии, которой выдан этот push
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("policies.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created()
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PushOutbox(Base):
    __tablename__ = "push_outbox"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    auth_request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth_requests.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16), default="queued")          # queued | sent | failed | skipped
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)              # SHA-256 токена cookie
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    mfa_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    csrf: Mapped[str] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    event: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="info")          # info | warning | critical
    actor: Mapped[str] = mapped_column(String(255), default="system")
    target: Mapped[str | None] = mapped_column(String(255))
    ip: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[str] = mapped_column(String(16), default="ok")
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict)


class RateLimit(Base):
    """Счётчик попыток в окне времени — общий для всех экземпляров api (в памяти процесса лимит умножался бы)."""
    __tablename__ = "rate_limits"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    hits: Mapped[int] = mapped_column(Integer, default=0)


class Heartbeat(Base):
    __tablename__ = "heartbeats"
    service: Mapped[str] = mapped_column(String(32), primary_key=True)          # radius | worker
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    info: Mapped[dict] = mapped_column(JSONB, default=dict)


__all__ = ["Base", "User", "Group", "UserGroup", "Device", "TotpFactor", "OneTimeToken", "Resource", "RadiusClient", "ApiClient",
           "LdapDirectory", "Policy", "AuthRequest", "PushOutbox", "AdminSession", "AuditEvent", "Setting", "RateLimit", "Heartbeat", "UniqueConstraint"]
