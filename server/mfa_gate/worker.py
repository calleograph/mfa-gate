"""Фоновый процесс: доставка push, истечение запросов, очистка по срокам хранения, пульс."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from .config import settings
from .db import sessionmaker
from .models import AdminSession, AuditEvent, RateLimit, AuthRequest, Device, Heartbeat, PushOutbox
from .security.crypto import box
from .services import authflow, events
from .services.push import PushError, build_providers

log = logging.getLogger("mfa_gate.worker")
MAX_ATTEMPTS = 3


async def _send_one(providers: dict, http: httpx.AsyncClient, item: PushOutbox, device: Device, req: AuthRequest) -> bool:
    provider = providers.get(device.push_provider)
    if req.status != "pending" or device.status != "active" or provider is None or not device.push_token_enc:
        item.status = "skipped"            # устройство без push-канала забирает запрос long-poll'ом
        return False
    item.attempts += 1
    try:
        token = box().decrypt(device.push_token_enc, aad=b"push:" + str(device.id).encode())
        await provider.send(http, token, str(req.id), str(device.id))
        item.status, item.error = "sent", None
        return True
    except PushError as exc:
        item.error = str(exc)
        if exc.invalid_token:
            device.push_token_enc, device.push_provider = None, "none"
        item.status = "failed" if exc.invalid_token or item.attempts >= MAX_ATTEMPTS else "queued"
    except Exception as exc:  # noqa: BLE001 — сбой одного устройства (в том числе неожиданный) не должен останавливать доставку остальным
        item.error = type(exc).__name__
        item.status = "failed" if item.attempts >= MAX_ATTEMPTS else "queued"
    return False


async def deliver(providers: dict, http: httpx.AsyncClient) -> int:
    async with sessionmaker()() as session:
        rows = (await session.execute(
            select(PushOutbox, Device, AuthRequest)
            .join(Device, Device.id == PushOutbox.device_id).join(AuthRequest, AuthRequest.id == PushOutbox.auth_request_id)
            .where(PushOutbox.status == "queued").order_by(PushOutbox.id).limit(50).with_for_update(of=PushOutbox, skip_locked=True))).all()
        # параллельно: один медленный канал не задерживает остальные входы
        results = await asyncio.gather(*(_send_one(providers, http, item, device, req) for item, device, req in rows))
        await session.commit()
    return sum(results)


async def housekeeping() -> None:
    cfg = settings()
    now = datetime.now(UTC)
    async with sessionmaker()() as session:
        stale = (await session.scalars(select(AuthRequest.id).where(AuthRequest.status == "pending", AuthRequest.expires_at < now))).all()
        for request_id in stale:
            await authflow.expire(session, request_id)
        await session.execute(delete(AuthRequest).where(AuthRequest.created_at < now - timedelta(days=cfg.auth_request_retention_days)))
        await session.execute(delete(AdminSession).where(AdminSession.expires_at < now))
        await session.execute(delete(RateLimit).where(RateLimit.window_start < now - timedelta(hours=1)))
        await session.execute(delete(AuditEvent).where(AuditEvent.at < now - timedelta(days=cfg.audit_retention_days)))
        await session.execute(update(PushOutbox).where(PushOutbox.status == "queued", PushOutbox.created_at < now - timedelta(minutes=10))
                              .values(status="failed", error="timeout"))
        stmt = insert(Heartbeat).values(service="worker", seen_at=now, info={})
        await session.execute(stmt.on_conflict_do_update(index_elements=["service"], set_={"seen_at": stmt.excluded.seen_at}))
        await session.commit()


async def run() -> None:
    events.hub.start()
    providers = build_providers()
    log.info("worker запущен, push-провайдеры: %s", ", ".join(providers) or "нет (только long-poll)")
    last_housekeeping = 0.0
    async with httpx.AsyncClient(timeout=5) as http:
        while True:
            try:
                await deliver(providers, http)
            except Exception:
                log.exception("доставка push завершилась ошибкой")
            try:
                if time.monotonic() - last_housekeeping > 10:
                    await housekeeping()
                    last_housekeeping = time.monotonic()
                    with open("/tmp/worker.alive", "w") as fh:  # noqa: S108 — tmpfs контейнера
                        fh.write(str(int(time.time())))
            except Exception:
                log.exception("цикл worker завершился ошибкой")
            await events.hub.wait("push", 2.0)
