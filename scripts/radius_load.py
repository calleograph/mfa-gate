#!/usr/bin/env python3
"""Нагрузочная проверка RADIUS на работающем стенде: отказы, TOTP через Access-Challenge, push с подтверждением телефонами.

  cd server && .venv/bin/python ../scripts/radius_load.py --url http://127.0.0.1:18480 --radius 127.0.0.1:18120 \
      --key <key_id:secret со scopes all> [--users 40] [--concurrency 40] [--client-cidr 127.0.0.1/32]

Создаёт свой ресурс, RADIUS-клиента и пользователей (имена с уникальным суффиксом), печатает задержки p50/p95/p99 и итог.
Код возврата 1, если хоть один ответ не тот, что ожидался.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import secrets
import statistics
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

import httpx  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402

from mfa_gate.radius import packet as rp  # noqa: E402
from mfa_gate.security import totp  # noqa: E402

PASSWORD = "Load-Test-Password-1"


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class Udp(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()

    def datagram_received(self, data, addr) -> None:
        if not self.future.done():
            self.future.set_result(data)


async def radius(addr, secret: str, user: str, password: str, *, state: bytes | None = None, calling: str = "198.51.100.50",
                 timeout: float = 60) -> tuple[int | None, float, bytes | None]:
    attrs = [(rp.USER_NAME, user.encode()), (rp.USER_PASSWORD, password.encode()), (rp.CALLING_STATION_ID, calling.encode())]
    if state:
        attrs.append((rp.STATE, state))
    data = rp.encode_request(secrets.randbelow(256), secret.encode(), attrs)
    loop = asyncio.get_running_loop()
    transport, proto = await loop.create_datagram_endpoint(Udp, remote_addr=addr)
    started = time.monotonic()
    try:
        transport.sendto(data)
        reply = await asyncio.wait_for(proto.future, timeout)
    except TimeoutError:
        return None, time.monotonic() - started, None
    finally:
        transport.close()
    pkt = rp.decode(reply)
    assert rp.verify_response(reply, data[4:20], secret.encode())
    return pkt.code, time.monotonic() - started, pkt.get(rp.STATE)


def summary(name: str, latencies: list[float], wall: float, errors: int) -> dict:
    lat = sorted(latencies)
    q = (lambda p: round(lat[min(len(lat) - 1, int(p * len(lat)))] * 1000, 1)) if lat else (lambda p: None)
    out = {"scenario": name, "requests": len(lat), "errors": errors, "rps": round(len(lat) / wall, 1) if wall else None,
           "p50_ms": q(0.50), "p95_ms": q(0.95), "p99_ms": q(0.99), "mean_ms": round(statistics.mean(lat) * 1000, 1) if lat else None}
    print(json.dumps(out, ensure_ascii=False))
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--radius", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--users", type=int, default=40)
    ap.add_argument("--concurrency", type=int, default=40)
    ap.add_argument("--client-cidr", default="127.0.0.1/32")
    args = ap.parse_args()
    host, port = args.radius.rsplit(":", 1)
    addr = (host, int(port))
    run = secrets.token_hex(3)
    key_id, key_secret = args.key.split(":", 1)
    api = httpx.AsyncClient(base_url=args.url, auth=(key_id, key_secret), timeout=60)
    dev = httpx.AsyncClient(base_url=args.url, timeout=60)

    for old in (await api.get("/api/v1/radius-clients")).json()["items"]:   # один адрес — один клиент
        if old["address"] == args.client_cidr and old["name"].startswith("load-"):
            await api.delete(f"/api/v1/radius-clients/{old['id']}")
    res = (await api.post("/api/v1/resources", json={"name": f"Load {run}"})).json()
    rc = (await api.post("/api/v1/radius-clients", json={"resource_id": res["id"], "name": f"load-{run}", "address": args.client_cidr})).json()
    if "secret" not in rc:
        print("не удалось создать RADIUS-клиента:", rc)
        return 1
    secret = rc["secret"]

    # пользователи: половина с TOTP, половина с телефоном
    sem = asyncio.Semaphore(10)

    async def make_user(i: int) -> dict:
        async with sem:
            u = (await api.post("/api/v1/users", json={"login": f"load{i}-{run}", "password": PASSWORD})).json()
            if i % 2 == 0:
                t = (await api.post(f"/api/v1/users/{u['id']}/totp")).json()
                await api.post(f"/api/v1/users/{u['id']}/totp/{t['factor_id']}/confirm", json={"code": totp.code_at(t["secret"])})
                return {"login": u["login"], "kind": "totp", "secret": t["secret"]}
            issued = (await api.post(f"/api/v1/users/{u['id']}/enrollment")).json()
            token = parse_qs(urlparse(issued["uri"]).query)["token"][0]
            key = ec.generate_private_key(ec.SECP256R1())
            spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
            # у каждого «телефона» свой адрес: лимит привязок — 10 в минуту с адреса
            d = (await dev.post("/api/device/v1/enroll", json={"token": token, "device_name": "load", "public_key": b64u(spki)},
                                headers={"X-Forwarded-For": f"203.0.113.{i % 250 + 1}"})).json()
            return {"login": u["login"], "kind": "push", "key": key, "device_id": d["device_id"], "device_secret": d["device_secret"]}

    users = await asyncio.gather(*(make_user(i) for i in range(args.users)))
    # ждём, пока RADIUS подхватит клиента
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline and (await radius(addr, secret, f"ghost-{run}", "x", timeout=2))[0] is None:
        pass
    await asyncio.sleep(31 - time.time() % 30)   # TOTP-коды подтверждения уже использованы — ждём следующий шаг
    results = []
    gate = asyncio.Semaphore(args.concurrency)

    # A. поток отказов (неверный пароль) — проверка пароля и ответ без ожиданий
    async def reject(u: dict):
        async with gate:
            return await radius(addr, secret, u["login"], "wrong-password")
    started = time.monotonic()
    out = await asyncio.gather(*(reject(users[i % len(users)]) for i in range(args.users * 3)))
    results.append(summary("A: неверный пароль → Access-Reject", [o[1] for o in out], time.monotonic() - started,
                           sum(1 for o in out if o[0] != rp.ACCESS_REJECT)))
    # счётчики блокировки после потока ошибок не должны мешать следующим сценариям
    for u in (await api.get("/api/v1/users", params={"q": run, "size": 200})).json()["items"]:
        await api.post(f"/api/v1/users/{u['id']}/unlock")

    # B. TOTP: Access-Request → Access-Challenge → код → Access-Accept
    async def totp_flow(u: dict):
        async with gate:
            code, t1, state = await radius(addr, secret, u["login"], PASSWORD)
            if code != rp.ACCESS_CHALLENGE:
                return None, t1
            code2, t2, _ = await radius(addr, secret, u["login"], totp.code_at(u["secret"]), state=state)
            return code2, t1 + t2
    started = time.monotonic()
    out = await asyncio.gather(*(totp_flow(u) for u in users if u["kind"] == "totp"))
    results.append(summary("B: TOTP через Access-Challenge → Access-Accept", [o[1] for o in out], time.monotonic() - started,
                           sum(1 for o in out if o[0] != rp.ACCESS_ACCEPT)))

    # C. push: RADIUS держит запрос, «телефоны» long-poll'ом получают запрос и подписывают подтверждение
    decided: dict[str, int] = {}

    async def phone(u: dict):
        auth = {"Authorization": f"Device {u['device_id']}:{u['device_secret']}"}
        for _ in range(20):
            items = (await dev.get("/api/device/v1/requests", params={"status": "pending", "wait": 10}, headers=auth)).json()["items"]
            for item in items:
                msg = "\n".join(["mfa-gate-decision-v1", item["id"], u["device_id"], item["challenge"], "approve"]).encode()
                sig = b64u(u["key"].sign(msg, ec.ECDSA(hashes.SHA256())))
                r = await dev.post(f"/api/device/v1/requests/{item['id']}/decision", headers=auth, json={"decision": "approve", "signature": sig})
                decided[item["id"]] = decided.get(item["id"], 0) + (r.status_code == 200)
                # повтор той же подписи обязан получить отказ (защита от повторного использования подтверждения)
                again = await dev.post(f"/api/device/v1/requests/{item['id']}/decision", headers=auth, json={"decision": "approve", "signature": sig})
                if again.status_code != 409:
                    decided[item["id"]] = -100
                return

    async def push_flow(u: dict):
        async with gate:
            return await radius(addr, secret, u["login"], PASSWORD, calling=f"198.51.100.{secrets.randbelow(200) + 20}")
    started = time.monotonic()
    push_users = [u for u in users if u["kind"] == "push"]
    phones = [asyncio.create_task(phone(u)) for u in push_users]
    out = await asyncio.gather(*(push_flow(u) for u in push_users))
    await asyncio.gather(*phones)
    bad_decisions = sum(1 for v in decided.values() if v != 1)
    results.append(summary("C: push с подписанным подтверждением → Access-Accept", [o[1] for o in out], time.monotonic() - started,
                           sum(1 for o in out if o[0] != rp.ACCESS_ACCEPT) + bad_decisions))

    await api.delete(f"/api/v1/radius-clients/{rc['id']}")   # узкая сеть клиента не должна перекрывать другие проверки
    errors = sum(r["errors"] for r in results)
    print(json.dumps({"total_errors": errors, "users": len(users), "concurrency": args.concurrency}, ensure_ascii=False))
    await api.aclose()
    await dev.aclose()
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
