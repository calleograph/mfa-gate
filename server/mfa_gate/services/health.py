"""Проверки состояния для панели и CLI: каждая — статус, понятное сообщение и что делать."""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Device, Heartbeat, LdapDirectory, RadiusClient
from ..security.crypto import box
from .push import build_providers

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


def _check(name: str, status: str, message: str, action: str = "") -> dict:
    return {"name": name, "status": status, "message": message, "action": action}


def migration_head() -> str | None:
    from alembic.config import Config   # noqa: PLC0415
    from alembic.script import ScriptDirectory   # noqa: PLC0415
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    return ScriptDirectory.from_config(cfg).get_current_head()


async def _tcp(host: str, port: int, timeout: float = 2.0) -> str | None:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        writer.close()
        return None
    except (OSError, TimeoutError) as exc:
        return type(exc).__name__


async def checks(session: AsyncSession) -> dict:
    cfg = settings()
    out: list[dict] = []
    started = time.monotonic()
    version = await session.scalar(text("SHOW server_version"))
    out.append(_check("База данных", "ok", f"PostgreSQL {version}, ответ за {int((time.monotonic() - started) * 1000)} мс"))

    current = await session.scalar(text("SELECT version_num FROM alembic_version"))
    head = migration_head()
    out.append(_check("Миграции", "ok" if current == head else "error", f"схема {current}, ожидается {head}",
                      "" if current == head else "Выполните ./mfagatectl update (миграции применяются перед запуском api)"))

    try:
        box().decrypt(box().encrypt("probe"))
        out.append(_check("Мастер-ключ", "ok", "Ключ шифрования загружен"))
    except Exception:  # noqa: BLE001
        out.append(_check("Мастер-ключ", "error", "Ключ не задан или повреждён", "Проверьте MFA_GATE_MASTER_KEY_FILE и права на файл"))

    beats = {h.service: h for h in (await session.scalars(select(Heartbeat))).all()}
    now = datetime.now(UTC)
    for service, label, action in (("radius", "RADIUS-сервер", "./mfagatectl logs radius"), ("worker", "Фоновые задачи и push", "./mfagatectl logs worker")):
        beat = beats.get(service)
        age = (now - beat.seen_at).total_seconds() if beat else None
        if age is None:
            out.append(_check(label, "error", "ни разу не отметился", f"Проверьте контейнер: {action}"))
        elif age > 60:
            out.append(_check(label, "error", f"последний сигнал {int(age)} с назад", f"Проверьте контейнер: {action}"))
        else:
            info = beat.info or {}
            detail = (f"клиентов {info.get('clients', 0)}, запросов {info.get('requests', 0)}, отброшено {info.get('dropped', 0)}"
                      if service == "radius" else "работает")
            out.append(_check(label, "ok", detail))

    providers = build_providers()
    no_push = await session.scalar(select(func.count()).select_from(Device).where(Device.status == "active", Device.push_provider == "none"))
    out.append(_check("Доставка push", "ok" if providers else "warning",
                      (f"провайдеры: {', '.join(providers)}" if providers else "провайдеры не настроены — телефоны получают запросы только при открытом приложении")
                      + (f"; устройств без push-канала: {no_push}" if no_push else ""),
                      "" if providers else "Задайте MFA_GATE_PUSH_FCM_CREDENTIALS_FILE (см. docs/push.md)"))

    if not cfg.public_url.startswith("https://"):
        out.append(_check("Публичный адрес", "warning", f"{cfg.public_url} — не HTTPS", "Приложение в рабочей сборке подключается только по HTTPS"))
    else:
        out.append(_check("Публичный адрес", "ok", cfg.public_url))

    clients = await session.scalar(select(func.count()).select_from(RadiusClient).where(RadiusClient.enabled))
    out.append(_check("RADIUS-клиенты", "ok" if clients else "warning", f"активных: {clients}",
                      "" if clients else "Добавьте VPN-шлюз в разделе «RADIUS-клиенты»"))

    for directory in (await session.scalars(select(LdapDirectory).where(LdapDirectory.enabled))).all():
        results = await asyncio.gather(*(_tcp(h, directory.port) for h in directory.hosts))
        down = [f"{h} ({err})" for h, err in zip(directory.hosts, results, strict=True) if err]
        status = "ok" if not down else ("error" if len(down) == len(directory.hosts) else "warning")
        out.append(_check(f"Каталог «{directory.name}»", status,
                          "все контроллеры доступны по TCP" if not down else "недоступны: " + ", ".join(down),
                          "" if not down else "Подробная проверка: раздел LDAP → «Диагностика»"))
        if directory.encryption == "none" or not directory.verify_tls:
            out.append(_check(f"Каталог «{directory.name}»: шифрование", "warning", "пароли идут без проверенного TLS",
                              "Включите LDAPS и проверку сертификата"))

    worst = "error" if any(c["status"] == "error" for c in out) else ("warning" if any(c["status"] == "warning" for c in out) else "ok")
    return {"status": worst, "checks": out, "version": cfg.version, "checked_at": now.isoformat()}
