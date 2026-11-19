"""RADIUS-сервер аутентификации (UDP 1812, PAP + Access-Challenge для OTP).

- Клиент определяется по адресу источника (самая узкая подходящая сеть); неизвестный адрес — пакет молча отбрасывается (RFC 2865).
- Message-Authenticator обязателен по умолчанию (защита от BlastRADIUS); неверный — пакет отбрасывается: так проявляется неверный секрет.
- Повтор пакета (тот же адрес, Identifier и Request Authenticator) не создаёт новую транзакцию: ждёт ту же обработку и получает тот же ответ.
- Push: запрос держится открытым до решения или push_timeout; таймаут шлюза должен быть больше.
- first_factor=gateway (docs/adr/0016): пароль проверил сам шлюз; пустой User-Password — push или Access-Challenge, 6–8 цифр — OTP.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from ..config import settings
from ..db import sessionmaker
from ..models import Heartbeat, RadiusClient, Resource
from ..security.crypto import box
from ..services import authflow, events
from ..services.audit import audit
from . import packet as rp

log = logging.getLogger("mfa_gate.radius")
OTP_RE = re.compile(r"\d{6,8}")
DEDUP_TTL = 90.0          # дольше, чем push_timeout + повторы шлюза
CLIENTS_RELOAD = 15.0


@dataclass
class Client:
    network: ipaddress.IPv4Network | ipaddress.IPv6Network
    row: RadiusClient
    secret: bytes
    resource: Resource


class RadiusProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.transport: asyncio.DatagramTransport | None = None
        self.clients: list[Client] = []
        self.loaded_at = 0.0
        self.inflight: dict[tuple, tuple[float, asyncio.Future]] = {}
        self.tasks: set[asyncio.Task] = set()
        self.bad_ma_logged: dict = {}
        self.stats = {"requests": 0, "accepts": 0, "rejects": 0, "challenges": 0, "dropped": 0, "duplicates": 0}

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        task = asyncio.create_task(self._handle(data, addr))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def load_clients(self, force: bool = False) -> None:
        if not force and time.monotonic() - self.loaded_at < CLIENTS_RELOAD:
            return
        async with sessionmaker()() as session:
            rows = (await session.execute(select(RadiusClient, Resource).join(Resource, Resource.id == RadiusClient.resource_id)
                                          .where(RadiusClient.enabled))).all()
        clients = []
        for row, resource in rows:
            try:
                network = ipaddress.ip_network(row.address, strict=False)
                secret = box().decrypt(row.secret_enc, aad=b"radius:" + str(row.id).encode()).encode()
            except Exception:  # noqa: BLE001 — одна битая запись не должна останавливать сервер
                log.error("RADIUS-клиент %s пропущен: неверный адрес или секрет", row.name)
                continue
            clients.append(Client(network, row, secret, resource))
        clients.sort(key=lambda c: c.network.prefixlen, reverse=True)
        self.clients, self.loaded_at = clients, time.monotonic()

    def _client_for(self, ip: str) -> Client | None:
        addr = ipaddress.ip_address(ip)
        if getattr(addr, "ipv4_mapped", None):
            addr = addr.ipv4_mapped
        return next((c for c in self.clients if addr.version == c.network.version and addr in c.network), None)

    def _send(self, data: bytes, addr) -> None:
        if self.transport is not None:
            self.transport.sendto(data, addr)

    async def _handle(self, data: bytes, addr) -> None:
        self.stats["requests"] += 1
        try:
            await self.load_clients()
        except Exception as exc:  # noqa: BLE001 — БД недоступна: молчим, шлюз сочтёт сервер недоступным (отказ по умолчанию)
            log.warning("список клиентов не загружен: %s", type(exc).__name__)
            if not self.clients:
                return
        client = self._client_for(addr[0])
        if client is None:
            self.stats["dropped"] += 1
            log.warning("пакет от неизвестного клиента %s отброшен", addr[0])
            return
        try:
            pkt = rp.decode(data)
        except rp.PacketError as exc:
            self.stats["dropped"] += 1
            log.warning("некорректный пакет от %s: %s", addr[0], exc)
            return
        if pkt.code != rp.ACCESS_REQUEST:
            self.stats["dropped"] += 1
            return

        # подлинность — до кэша повторов: мусор и подделки не должны его раздувать
        ma = rp.verify_message_authenticator(data, pkt, client.secret)
        if ma is False or (ma is None and settings().radius_require_message_authenticator):
            self.stats["dropped"] += 1
            await self._audit_bad_authenticator(client, addr, "missing" if ma is None else "invalid")
            return
        key = (addr[0], addr[1], pkt.identifier, pkt.authenticator)
        now = time.monotonic()
        while self.inflight:   # словарь упорядочен по времени вставки — чистим только устаревшее начало
            oldest = next(iter(self.inflight))
            if now - self.inflight[oldest][0] < DEDUP_TTL:
                break
            del self.inflight[oldest]
        if key in self.inflight:
            self.stats["duplicates"] += 1
            future = self.inflight[key][1]
            if future.done() and future.result():
                self._send(future.result(), addr)   # ответ потерялся в сети — повторяем тот же
            return
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.inflight[key] = (now, future)
        response = None
        try:
            response = await self._process(data, pkt, client, addr)
        except Exception:
            log.exception("ошибка обработки RADIUS-запроса")
            response = rp.encode_response(rp.ACCESS_REJECT, pkt, client.secret, [(rp.REPLY_MESSAGE, "Внутренняя ошибка".encode())])
        finally:
            future.set_result(response)
        if response:
            self._send(response, addr)

    async def _audit_bad_authenticator(self, client: Client, addr, reason: str) -> None:
        """Не чаще раза в минуту на клиента: поток подделок не должен превращаться в поток записей в БД."""
        now = time.monotonic()
        if now - self.bad_ma_logged.get(client.row.id, -3600.0) < 60:
            return
        self.bad_ma_logged[client.row.id] = now
        log.warning("пакет от %s (%s) отброшен: Message-Authenticator %s", addr[0], client.row.name, reason)
        try:
            async with sessionmaker()() as session:
                audit(session, "radius.bad_authenticator", target=client.row.name, ip=addr[0], result="fail", severity="warning", reason=reason,
                      dropped_total=self.stats["dropped"])
                await session.commit()
        except Exception:
            log.exception("не удалось записать аудит")

    async def _process(self, data: bytes, pkt: rp.Packet, client: Client, addr) -> bytes | None:
        cfg = settings()
        nas_id = pkt.text(rp.NAS_IDENTIFIER)
        if client.row.nas_identifier and nas_id != client.row.nas_identifier:
            self.stats["dropped"] += 1
            log.warning("NAS-Identifier %r не совпал для клиента %s", nas_id, client.row.name)
            return None

        def reply(code: int, message: str = "", extra: list[tuple[int, bytes]] | None = None) -> bytes:
            self.stats[{rp.ACCESS_ACCEPT: "accepts", rp.ACCESS_REJECT: "rejects", rp.ACCESS_CHALLENGE: "challenges"}[code]] += 1
            attrs = list(extra or [])
            if message:
                attrs.append((rp.REPLY_MESSAGE, message.encode()[:253]))
            return rp.encode_response(code, pkt, client.secret, attrs)

        login = (pkt.text(rp.USER_NAME) or "").strip()
        raw_password = pkt.get(rp.USER_PASSWORD)
        gateway = client.row.first_factor == "gateway"   # пароль уже проверил этот шлюз (режимы «только логин» / «логин и код»)
        if not login or (raw_password is None and not gateway):
            return reply(rp.ACCESS_REJECT, "Поддерживается только PAP" if pkt.get(rp.CHAP_PASSWORD) else "Нет имени или пароля")
        try:
            password = rp.decrypt_password(raw_password, client.secret, pkt.authenticator) if raw_password is not None else ""
        except rp.PacketError:
            return reply(rp.ACCESS_REJECT, "Некорректный пароль")
        nas_ip = rp.decode_ip(pkt.get(rp.NAS_IP_ADDRESS)) or addr[0]
        source_ip = pkt.text(rp.CALLING_STATION_ID)
        smaker = sessionmaker()

        state = pkt.get(rp.STATE)
        if state is not None:   # ответ на Access-Challenge: State = id транзакции, пароль = OTP
            try:
                request_id = uuid.UUID(bytes=state)
            except ValueError:
                return reply(rp.ACCESS_REJECT, "Неизвестная транзакция")
            async with smaker() as session:
                req = await session.get(authflow.AuthRequest, request_id)
                if req is None or req.login != login.lower() or req.resource_id != client.resource.id:
                    return reply(rp.ACCESS_REJECT, "Неизвестная транзакция")
                outcome = await authflow.answer_challenge(session, request_id, password)
                await session.commit()
            return reply(rp.ACCESS_ACCEPT if outcome.status == "accept" else rp.ACCESS_REJECT, outcome.message)

        # в режиме шлюза поле пароля — пусто (нужен push или Access-Challenge) либо OTP; остальное не пароль для нас и игнорируется
        otp = password.strip() if gateway and OTP_RE.fullmatch(password.strip()) else None
        async with smaker() as session:
            outcome = await authflow.begin(session, login=login, password=None if gateway else password, otp=otp, resource=client.resource,
                                           channel="radius", client_name=client.row.name, source_ip=source_ip, nas_ip=nas_ip)
            await session.execute(update(RadiusClient).where(RadiusClient.id == client.row.id).values(last_seen_at=datetime.now(UTC)))
            await session.commit()

        if outcome.status == "accept":
            return reply(rp.ACCESS_ACCEPT)
        if outcome.status == "reject":
            return reply(rp.ACCESS_REJECT, outcome.message)
        if outcome.status == "challenge":
            return reply(rp.ACCESS_CHALLENGE, outcome.message,
                         [(rp.STATE, outcome.request_id.bytes), (rp.SESSION_TIMEOUT, cfg.otp_challenge_timeout.to_bytes(4, "big"))])

        # pending: ждём решения на телефоне
        async with smaker() as session:
            req = await session.get(authflow.AuthRequest, outcome.request_id)
            left = (req.expires_at - datetime.now(UTC)).total_seconds() if req and req.expires_at else 0
        req = await authflow.wait_decision(smaker, outcome.request_id, time.monotonic() + max(left, 0))
        if req is not None and req.status == "approved":
            return reply(rp.ACCESS_ACCEPT)
        if req is not None and req.status == "pending":
            async with smaker() as session:
                await authflow.expire(session, req.id)
                await session.commit()
            return reply(rp.ACCESS_REJECT, authflow.REASONS["push_expired"])
        return reply(rp.ACCESS_REJECT, authflow.REASONS.get(req.reason or "", "Вход отклонён") if req else "Вход отклонён")


async def heartbeat(proto: RadiusProtocol, alive_file: str = "/tmp/radius.alive") -> None:  # noqa: S108 — tmpfs контейнера, файл пульса для healthcheck
    while True:
        try:
            async with sessionmaker()() as session:
                stmt = insert(Heartbeat).values(service="radius", seen_at=datetime.now(UTC),
                                                info={**proto.stats, "clients": len(proto.clients)})
                await session.execute(stmt.on_conflict_do_update(index_elements=["service"],
                                                                 set_={"seen_at": stmt.excluded.seen_at, "info": stmt.excluded.info}))
                await session.commit()
            await proto.load_clients(force=True)
            with open(alive_file, "w") as fh:
                fh.write(str(int(time.time())))
        except Exception as exc:  # noqa: BLE001
            log.warning("heartbeat: %s", type(exc).__name__)
        await asyncio.sleep(10)


async def serve() -> None:
    cfg = settings()
    events.hub.start()
    loop = asyncio.get_running_loop()
    proto = RadiusProtocol()
    await proto.load_clients(force=True)
    transport, _ = await loop.create_datagram_endpoint(lambda: proto, local_addr=(cfg.radius_host, cfg.radius_port))
    log.info("RADIUS слушает %s:%s/udp, клиентов: %d", cfg.radius_host, cfg.radius_port, len(proto.clients))
    try:
        await heartbeat(proto)
    finally:
        transport.close()
