"""Зависимости API: адрес клиента, сессия администратора, ключи интеграций, устройство, права."""
from __future__ import annotations

import base64
import ipaddress
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..models import AdminSession, ApiClient, Device, User
from ..security.crypto import same_hash, sha256_hex

SESSION_COOKIE = "mfa_gate_session"
PORTAL_COOKIE = "mfa_gate_portal"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}

# Права: "<область>:read|write". Роли администраторов и scopes API-клиентов используют одни и те же строки.
AREAS = ("users", "groups", "devices", "resources", "policies", "radius", "ldap", "apiclients", "requests", "audit", "system", "admins", "auth")
ROLES: dict[str, set[str]] = {
    "superadmin": {"*"},
    "admin": {f"{a}:{m}" for a in AREAS if a != "admins" for m in ("read", "write")} | {"admins:read"},
    "operator": {"users:read", "users:write", "devices:read", "devices:write", "groups:read", "resources:read", "policies:read",
                 "requests:read", "audit:read", "system:read"},
    "auditor": {f"{a}:read" for a in AREAS},
}
ROLE_LABELS = {"superadmin": "Главный администратор", "admin": "Администратор", "operator": "Оператор", "auditor": "Аудитор"}


def allowed(perms: set[str], need: str) -> bool:
    return "*" in perms or need in perms


def default_gateway(route_table: str) -> str | None:
    """Шлюз по умолчанию из /proc/net/route (в контейнере это шлюз сети Docker, через который приходят опубликованные порты)."""
    for line in route_table.splitlines()[1:]:
        f = line.split()
        if len(f) > 2 and f[1] == "00000000" and f[2] != "00000000":
            return str(ipaddress.IPv4Address(int(f[2], 16).to_bytes(4, "little")))
    return None


@lru_cache
def _trusted() -> list:
    """auto — loopback и шлюз сети контейнера: reverse proxy на этом же хосте доверенный, узлы локальной сети — нет."""
    items = [n.strip() for n in settings().trusted_proxies.split(",") if n.strip()]
    if "auto" in items:
        items = [i for i in items if i != "auto"] + ["127.0.0.1/32", "::1/128"]
        try:   # шлюз доверенный только в контейнере; на «голом» сервере шлюз по умолчанию — роутер сети, ему доверять нельзя
            gw = default_gateway(open("/proc/net/route").read()) if os.path.exists("/.dockerenv") else None
            if gw:
                items.append(f"{gw}/32")
        except OSError:
            pass
    return [ipaddress.ip_network(n, strict=False) for n in items]


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "0.0.0.0"   # noqa: S104 — заглушка, если адрес клиента неизвестен
    try:
        if any(ipaddress.ip_address(peer) in net for net in _trusted()):
            forwarded = request.headers.get("x-forwarded-for", "")
            if forwarded:
                last = forwarded.split(",")[-1].strip()   # последний адрес добавлен нашим прокси
                return str(ipaddress.ip_address(last))     # мусор вместо адреса — ValueError, берём адрес соединения
    except ValueError:
        pass
    return peer


def is_https(request: Request) -> bool:
    """Схема запроса с учётом reverse proxy: X-Forwarded-Proto принимается только от доверенных адресов."""
    peer = request.client.host if request.client else ""
    try:
        if any(ipaddress.ip_address(peer) in net for net in _trusted()):
            proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
            if proto:
                return proto == "https"
    except ValueError:
        pass
    return request.url.scheme == "https"


def cookie_secure(request: Request) -> bool:
    mode = str(settings().cookie_secure).lower()
    return is_https(request) if mode == "auto" else mode in ("1", "true", "yes")


class RateLimiter:
    """Окно фиксированной длины в PostgreSQL: одно атомарное INSERT … ON CONFLICT … RETURNING на попытку, общий счёт для всех
    экземпляров api. Устаревшие окна удаляет worker."""

    def __init__(self, name: str, limit: int, window: int):
        self.name, self.limit, self.window = name, limit, window

    async def check(self, session: AsyncSession, key: str) -> None:
        hits = await session.scalar(text("""
            INSERT INTO rate_limits (key, window_start, hits)
            VALUES (:key, to_timestamp(floor(extract(epoch FROM now()) / :w) * :w), 1)
            ON CONFLICT (key) DO UPDATE SET
              hits = CASE WHEN rate_limits.window_start = EXCLUDED.window_start THEN rate_limits.hits + 1 ELSE 1 END,
              window_start = EXCLUDED.window_start
            RETURNING hits"""), {"key": f"{self.name}:{key}"[:128], "w": self.window})
        await session.commit()
        if hits > self.limit:
            raise HTTPException(429, {"code": "rate_limited", "message": "Слишком много попыток, повторите через минуту"})


login_limiter = RateLimiter("login", 20, 60)
enroll_limiter = RateLimiter("enroll", 10, 60)


@dataclass
class Principal:
    kind: str                     # admin | api
    name: str
    perms: set[str] = field(default_factory=set)
    user: User | None = None
    api_client: ApiClient | None = None
    ip: str = ""


async def _session_of(request: Request, session: AsyncSession, kind: str) -> tuple[AdminSession, User] | None:
    token = request.cookies.get(SESSION_COOKIE if kind == "admin" else PORTAL_COOKIE)
    if not token:
        return None
    row = await session.get(AdminSession, sha256_hex(token))
    # вид проверяется явно: сессия портала не должна открывать панель, даже если её токен подставят в cookie панели
    if row is None or row.kind != kind or row.expires_at <= datetime.now(UTC):
        return None
    user = await session.get(User, row.user_id)
    if user is None or user.status != "active" or (kind == "admin" and not user.admin_role):
        return None
    return row, user


async def admin_session(request: Request, session: AsyncSession = Depends(get_session)) -> tuple[AdminSession, User] | None:
    return await _session_of(request, session, "admin")


async def portal_session(request: Request, session: AsyncSession) -> tuple[AdminSession, User] | None:
    return await _session_of(request, session, "portal")


async def principal(request: Request, session: AsyncSession = Depends(get_session)) -> Principal:
    ip = client_ip(request)
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            key_id, secret = base64.b64decode(auth[6:]).decode().split(":", 1)
        except ValueError:
            raise HTTPException(401, {"code": "bad_credentials", "message": "Неверный формат ключа"}) from None
        client = await session.scalar(select(ApiClient).where(ApiClient.key_id == key_id))
        if client is None or not client.enabled or not same_hash(secret, client.secret_hash):
            raise HTTPException(401, {"code": "bad_credentials", "message": "Неверный ключ API"})
        if client.allowed_cidrs and not any(ipaddress.ip_address(ip) in ipaddress.ip_network(c, strict=False) for c in client.allowed_cidrs):
            raise HTTPException(403, {"code": "ip_not_allowed", "message": "Адрес не разрешён для этого ключа"})
        await session.execute(update(ApiClient).where(ApiClient.id == client.id).values(last_used_at=datetime.now(UTC)))
        await session.commit()
        return Principal("api", f"api:{client.name}", set(client.scopes or []), api_client=client, ip=ip)

    found = await admin_session(request, session)
    if found is None:
        raise HTTPException(401, {"code": "unauthenticated", "message": "Требуется вход"})
    row, user = found
    if not row.mfa_passed:
        raise HTTPException(401, {"code": "mfa_required", "message": "Требуется второй фактор"})
    if request.method in UNSAFE and request.headers.get("x-csrf-token") != row.csrf:
        raise HTTPException(403, {"code": "csrf", "message": "Нет CSRF-токена"})
    now = datetime.now(UTC)
    if row.expires_at - now < timedelta(minutes=settings().admin_session_minutes - 1):   # скользящий срок, без записи на каждый запрос
        row.expires_at = now + timedelta(minutes=settings().admin_session_minutes)
        await session.commit()
    return Principal("admin", user.login, ROLES.get(user.admin_role or "", set()), user=user, ip=ip)


def need(permission: str):
    async def check(p: Principal = Depends(principal)) -> Principal:
        if not allowed(p.perms, permission):
            raise HTTPException(403, {"code": "forbidden", "message": f"Нет права {permission}"})
        return p
    return check


async def device_auth(request: Request, session: AsyncSession = Depends(get_session)) -> Device:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Device "):
        raise HTTPException(401, {"code": "unauthenticated", "message": "Нет учётных данных устройства"})
    try:
        device_id, secret = auth[7:].split(":", 1)
        device = await session.get(Device, uuid.UUID(device_id))
    except ValueError:
        device = None
    if device is None or not same_hash(secret, device.secret_hash):
        raise HTTPException(401, {"code": "unauthenticated", "message": "Неизвестное устройство"})
    if device.status != "active":
        raise HTTPException(401, {"code": "device_revoked", "message": "Устройство отвязано"})
    owner = await session.get(User, device.user_id)
    if owner is None or owner.status != "active":
        raise HTTPException(401, {"code": "device_revoked", "message": "Учётная запись отключена"})
    now = datetime.now(UTC)
    if device.last_seen_at is None or now - device.last_seen_at > timedelta(minutes=1):
        device.last_seen_at = now
    # завершаем транзакцию: long-poll держит запрос до 30 с и не должен держать соединение из пула
    await session.commit()
    return device
