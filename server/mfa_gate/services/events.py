"""События между процессами через PostgreSQL LISTEN/NOTIFY (без Redis).

Ключи: `user:<uuid>` — у пользователя появился запрос (будит long-poll устройств и worker), `req:<uuid>` — по запросу принято решение
(будит ожидающий RADIUS/API). NOTIFY отправляется в транзакции и доставляется только после её фиксации.
"""
from __future__ import annotations

import asyncio
import logging

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import raw_dsn

CHANNEL = "mfa_gate"
log = logging.getLogger(__name__)


async def notify(session: AsyncSession, key: str) -> None:
    await session.execute(text("SELECT pg_notify(:c, :k)"), {"c": CHANNEL, "k": key})


class Hub:
    """Одно соединение LISTEN на процесс; ожидающие подписываются на ключ. При потере соединения переподключается, ожидающие
    в это время просыпаются по своему таймауту и перечитывают БД — событие не теряется, только задерживается."""

    def __init__(self) -> None:
        self._waiters: dict[str, set[asyncio.Event]] = {}
        self._task: asyncio.Task | None = None
        self.connected = False

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    def _on_notify(self, _conn, _pid, _channel, key: str) -> None:
        for event in self._waiters.get(key, ()):
            event.set()

    async def _run(self) -> None:
        while True:
            conn = None
            try:
                conn = await asyncio.wait_for(asyncpg.connect(raw_dsn()), 10)
                await conn.add_listener(CHANNEL, self._on_notify)
                self.connected = True
                while not conn.is_closed():
                    await asyncio.sleep(5)
                    await asyncio.wait_for(conn.execute("SELECT 1"), 5)   # полуоткрытое TCP-соединение не должно числиться живым
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("events: соединение LISTEN потеряно: %s", type(exc).__name__)
            finally:
                self.connected = False
                if conn is not None and not conn.is_closed():
                    conn.terminate()
            await asyncio.sleep(2)

    async def wait(self, key: str, timeout: float) -> bool:
        """True — пришло событие, False — истёк таймаут."""
        event = asyncio.Event()
        self._waiters.setdefault(key, set()).add(event)
        try:
            await asyncio.wait_for(event.wait(), timeout)
            return True
        except TimeoutError:
            return False
        finally:
            waiters = self._waiters.get(key)
            if waiters is not None:
                waiters.discard(event)
                if not waiters:
                    self._waiters.pop(key, None)


hub = Hub()
