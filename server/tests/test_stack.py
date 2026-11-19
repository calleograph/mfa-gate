"""Сквозные сценарии на работающем стенде (api + radius + worker + PostgreSQL).

Запуск: scripts/e2e.sh (поднимает изолированный стенд) или вручную с переменными:
  MFA_GATE_TEST_URL=http://127.0.0.1:18480 MFA_GATE_TEST_RADIUS=127.0.0.1:18120 MFA_GATE_TEST_KEY=<key_id:secret со scopes all>
  MFA_GATE_TEST_CLIENT_CIDR — сеть, с которой стенд видит тест (по умолчанию 127.0.0.0/8; в Docker — сеть моста)
  MFA_GATE_TEST_PUSH_TIMEOUT — значение MFA_GATE_PUSH_TIMEOUT стенда (по умолчанию 20)
Телефон эмулируется: ключ EC P-256 и подпись решения — как в Android-приложении (docs/protocol/device-api.md).
"""
from __future__ import annotations

import base64
import os
import secrets
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from mfa_gate.radius import packet as rp
from mfa_gate.security import totp

URL = os.environ.get("MFA_GATE_TEST_URL")
pytestmark = [pytest.mark.stack, pytest.mark.skipif(not URL, reason="нет MFA_GATE_TEST_URL — стенд не поднят")]
RADIUS = os.environ.get("MFA_GATE_TEST_RADIUS", "127.0.0.1:18120").rsplit(":", 1)
RADIUS_ADDR = (RADIUS[0], int(RADIUS[1]))
CIDR = os.environ.get("MFA_GATE_TEST_CLIENT_CIDR", "127.0.0.0/8")
PUSH_TIMEOUT = int(os.environ.get("MFA_GATE_TEST_PUSH_TIMEOUT", "20"))
RUN = secrets.token_hex(3)   # уникальный суффикс: тесты повторяемы на одном стенде


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


@dataclass
class Phone:
    device_id: str
    secret: str
    key: ec.EllipticCurvePrivateKey

    @property
    def auth(self) -> dict:
        return {"Authorization": f"Device {self.device_id}:{self.secret}"}

    def sign(self, item: dict, decision: str) -> str:
        msg = "\n".join(["mfa-gate-decision-v1", item["id"], self.device_id, item["challenge"], decision]).encode()
        return b64u(self.key.sign(msg, ec.ECDSA(hashes.SHA256())))


class Ctx:
    def __init__(self) -> None:
        key_id, secret = os.environ["MFA_GATE_TEST_KEY"].split(":", 1)
        self.api = httpx.Client(base_url=URL, auth=(key_id, secret), timeout=40)
        self.dev = httpx.Client(base_url=URL, timeout=40)
        self.radius_secret = ""
        self.users: dict[str, dict] = {}
        self.phones: dict[str, Phone] = {}

    def ok(self, resp: httpx.Response, status: int = 200) -> dict:
        assert resp.status_code == status, f"{resp.request.method} {resp.request.url} → {resp.status_code} {resp.text}"
        return resp.json() if resp.content else {}

    def user(self, name: str, **extra) -> dict:
        login = f"{name}-{RUN}"
        data = self.ok(self.api.post("/api/v1/users", json={"login": login, "password": "Correct-Horse-Battery-1", "display_name": name, **extra}), 201)
        self.users[name] = data
        return data

    def enroll(self, name: str) -> Phone:
        issued = self.ok(self.api.post(f"/api/v1/users/{self.users[name]['id']}/enrollment"))
        q = parse_qs(urlparse(issued["uri"]).query)
        assert base64.urlsafe_b64decode(q["url"][0] + "==").decode()
        key = ec.generate_private_key(ec.SECP256R1())
        spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        body = {"token": q["token"][0], "device_name": f"test-{name}", "platform": "android", "app_version": "test", "public_key": b64u(spki)}
        data = self.ok(self.dev.post("/api/device/v1/enroll", json=body))
        assert self.dev.post("/api/device/v1/enroll", json=body).status_code == 410, "ссылка привязки должна быть одноразовой"
        phone = Phone(data["device_id"], data["device_secret"], key)
        self.phones[name] = phone
        return phone

    def pending(self, phone: Phone, wait: int = 10) -> list[dict]:
        return self.ok(self.dev.get("/api/device/v1/requests", params={"status": "pending", "wait": wait}, headers=phone.auth))["items"]

    def decide(self, phone: Phone, item: dict, decision: str, signature: str | None = None) -> httpx.Response:
        return self.dev.post(f"/api/device/v1/requests/{item['id']}/decision", headers=phone.auth,
                             json={"decision": decision, "signature": signature or phone.sign(item, decision)})


def radius(user: str, password: str, secret: str, *, state: bytes | None = None, ma: bool = True, timeout: float = 5,
           calling: str = "198.51.100.7", raw: bytes | None = None, sock: socket.socket | None = None) -> tuple[rp.Packet | None, bytes]:
    attrs = [(rp.USER_NAME, user.encode()), (rp.USER_PASSWORD, password.encode()), (rp.NAS_IDENTIFIER, b"test-nas"),
             (rp.CALLING_STATION_ID, calling.encode())]
    if state:
        attrs.append((rp.STATE, state))
    data = raw or rp.encode_request(secrets.randbelow(256), secret.encode(), attrs, message_authenticator=ma)
    own = sock is None
    sock = sock or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(data, RADIUS_ADDR)
        reply, _ = sock.recvfrom(4096)
    except TimeoutError:
        return None, data
    finally:
        if own:
            sock.close()
    assert rp.verify_response(reply, data[4:20], secret.encode()), "Response Authenticator не сошёлся"
    pkt = rp.decode(reply)
    assert pkt.get(rp.MESSAGE_AUTHENTICATOR), "ответ без Message-Authenticator"
    return pkt, data


def in_thread(fn, *args, **kwargs):
    box: dict = {}
    t = threading.Thread(target=lambda: box.update(result=fn(*args, **kwargs)), daemon=True)
    t.start()
    return t, box


@pytest.fixture(scope="module")
def ctx() -> Ctx:
    c = Ctx()
    for old in c.ok(c.api.get("/api/v1/radius-clients"))["items"]:   # клиенты прошлых прогонов на том же адресе
        if old["address"] == str(__import__("ipaddress").ip_network(CIDR, strict=False)) and old["name"].startswith("gw-"):
            c.ok(c.api.delete(f"/api/v1/radius-clients/{old['id']}"), 204)
    # неизвестный RADIUS-клиент: до регистрации нашего адреса пакет отбрасывается молча
    probe_secret = "probe-secret-" + RUN + "x" * 8
    resp, _ = radius("nobody", "x", probe_secret, timeout=2)
    if resp is None:
        c.unknown_client_dropped = True
    else:
        c.unknown_client_dropped = None   # адрес теста уже зарегистрирован прошлым прогоном — проверка неприменима
    res = c.ok(c.api.post("/api/v1/resources", json={"name": f"VPN {RUN}", "kind": "vpn"}), 201)
    c.resource = res
    rc = c.ok(c.api.post("/api/v1/radius-clients", json={"resource_id": res["id"], "name": f"gw-{RUN}", "address": CIDR}), 201)
    c.radius_secret, c.radius_client = rc["secret"], rc
    # ждём, пока RADIUS подхватит клиента (перечитывает список раз в 10-15 с)
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        pkt, _ = radius(f"ghost-{RUN}", "x", c.radius_secret, timeout=2)
        if pkt is not None:
            break
    else:
        pytest.fail("RADIUS не подхватил нового клиента за 40 с")
    return c


def test_unknown_radius_client_dropped(ctx):
    if ctx.unknown_client_dropped is None:
        pytest.skip("адрес теста уже зарегистрирован прошлым прогоном на этом стенде")
    assert ctx.unknown_client_dropped


def test_wrong_shared_secret_dropped(ctx):
    ctx.user("eve")
    pkt, _ = radius(f"eve-{RUN}", "Correct-Horse-Battery-1", "wrong-secret-0000000000", timeout=2)
    assert pkt is None, "пакет с неверным секретом (Message-Authenticator не сходится) должен отбрасываться"


def test_missing_message_authenticator_dropped(ctx):
    pkt, _ = radius(f"eve-{RUN}", "Correct-Horse-Battery-1", ctx.radius_secret, ma=False, timeout=2)
    assert pkt is None


def test_wrong_password_and_unknown_user_rejected(ctx):
    wrong, _ = radius(f"eve-{RUN}", "bad-password", ctx.radius_secret)
    unknown, _ = radius(f"nobody-{RUN}", "whatever", ctx.radius_secret)
    assert wrong.code == unknown.code == rp.ACCESS_REJECT
    # по тексту ответа нельзя понять, существует ли логин
    assert wrong.text(rp.REPLY_MESSAGE) == unknown.text(rp.REPLY_MESSAGE)


def test_user_without_factor_rejected(ctx):
    pkt, _ = radius(f"eve-{RUN}", "Correct-Horse-Battery-1", ctx.radius_secret)
    assert pkt.code == rp.ACCESS_REJECT and "фактор" in (pkt.text(rp.REPLY_MESSAGE) or "")
    listed = ctx.ok(ctx.api.get("/api/v1/users", params={"q": RUN, "factor": "none", "size": 200}))["items"]
    assert f"eve-{RUN}" in {u["login"] for u in listed} and all(not u["devices"] and not u["totp"] for u in listed)


def test_push_approve_end_to_end(ctx):
    ctx.user("alice")
    phone = ctx.enroll("alice")
    assert ctx.pending(phone, wait=0) == []
    t, box = in_thread(radius, f"alice-{RUN}", "Correct-Horse-Battery-1", ctx.radius_secret, timeout=PUSH_TIMEOUT + 10)
    items = ctx.pending(phone, wait=10)
    assert len(items) == 1 and items[0]["resource"] == ctx.resource["name"] and items[0]["source_ip"] == "198.51.100.7"
    started = time.monotonic()
    assert ctx.ok(ctx.decide(phone, items[0], "approve"))["status"] == "approved"
    t.join()
    pkt, _ = box["result"]
    assert pkt.code == rp.ACCESS_ACCEPT
    assert time.monotonic() - started < 3, "RADIUS должен ответить сразу после решения (LISTEN/NOTIFY)"
    # повтор той же подписи — отказ
    assert ctx.decide(phone, items[0], "approve", signature=phone.sign(items[0], "approve")).status_code == 409


def test_push_deny(ctx):
    phone = ctx.phones["alice"]
    t, box = in_thread(radius, f"alice-{RUN}", "Correct-Horse-Battery-1", ctx.radius_secret, timeout=PUSH_TIMEOUT + 10)
    item = ctx.pending(phone)[0]
    assert ctx.ok(ctx.decide(phone, item, "deny"))["status"] == "denied"
    t.join()
    assert box["result"][0].code == rp.ACCESS_REJECT


def test_bad_signature_and_foreign_request(ctx):
    ctx.user("bob")
    bob = ctx.enroll("bob")
    alice = ctx.phones["alice"]
    t, box = in_thread(radius, f"alice-{RUN}", "Correct-Horse-Battery-1", ctx.radius_secret, timeout=PUSH_TIMEOUT + 10)
    item = ctx.pending(alice)[0]
    # подпись решения «deny», выданная за «approve»
    assert ctx.decide(alice, item, "approve", signature=alice.sign(item, "deny")).status_code == 400
    # чужое устройство не видит и не может подтвердить запрос
    assert ctx.pending(bob, wait=0) == []
    assert ctx.decide(bob, item, "approve", signature=bob.sign({**item}, "approve")).status_code == 404
    assert ctx.ok(ctx.decide(alice, item, "approve"))["status"] == "approved"
    t.join()
    assert box["result"][0].code == rp.ACCESS_ACCEPT


def test_retransmission_does_not_create_second_push(ctx):
    phone = ctx.phones["alice"]
    login = f"alice-{RUN}"
    data = rp.encode_request(42, ctx.radius_secret.encode(), [(rp.USER_NAME, login.encode()), (rp.USER_PASSWORD, b"Correct-Horse-Battery-1"),
                                                              (rp.CALLING_STATION_ID, b"198.51.100.8")])
    gw = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)   # шлюз повторяет пакет с того же адреса и порта (RFC 5080)
    t1, box1 = in_thread(radius, login, "", ctx.radius_secret, raw=data, timeout=PUSH_TIMEOUT + 10, sock=gw)
    first = ctx.pending(phone)
    gw.sendto(data, RADIUS_ADDR)                              # повтор в процессе ожидания — не должен создать вторую транзакцию
    # новый Access-Request той же попытки (другой Authenticator) присоединяется к открытой транзакции
    t3, box3 = in_thread(radius, login, "Correct-Horse-Battery-1", ctx.radius_secret, calling="198.51.100.8", timeout=PUSH_TIMEOUT + 10)
    time.sleep(1.5)
    items = ctx.pending(phone, wait=0)
    assert len(first) == 1 and len(items) == 1 and items[0]["id"] == first[0]["id"]
    ctx.ok(ctx.decide(phone, items[0], "approve"))
    for t in (t1, t3):
        t.join()
    assert box1["result"][0].code == rp.ACCESS_ACCEPT and box3["result"][0].code == rp.ACCESS_ACCEPT
    # повтор после ответа получает тот же (кэшированный) ответ, а не новую транзакцию
    again, _ = radius(login, "", ctx.radius_secret, raw=data, timeout=3, sock=gw)
    gw.close()
    assert again is not None and again.code == rp.ACCESS_ACCEPT
    history = ctx.ok(ctx.api.get("/api/v1/auth-requests", params={"login": login}))["items"]
    assert sum(1 for r in history if r["source_ip"] == "198.51.100.8") == 1


def test_push_timeout_rejects_and_late_approve_fails(ctx):
    phone = ctx.phones["alice"]
    t, box = in_thread(radius, f"alice-{RUN}", "Correct-Horse-Battery-1", ctx.radius_secret, calling="198.51.100.9",
                       timeout=PUSH_TIMEOUT + 15)
    item = ctx.pending(phone)[0]
    t.join()
    assert box["result"][0].code == rp.ACCESS_REJECT
    assert ctx.decide(phone, item, "approve").status_code in (409, 410)


def test_concurrent_users(ctx):
    alice, bob = ctx.phones["alice"], ctx.phones["bob"]
    ta, ba = in_thread(radius, f"alice-{RUN}", "Correct-Horse-Battery-1", ctx.radius_secret, calling="198.51.100.20", timeout=PUSH_TIMEOUT + 10)
    tb, bb = in_thread(radius, f"bob-{RUN}", "Correct-Horse-Battery-1", ctx.radius_secret, calling="198.51.100.21", timeout=PUSH_TIMEOUT + 10)
    ia, ib = ctx.pending(alice)[0], ctx.pending(bob)[0]
    ctx.ok(ctx.decide(bob, ib, "deny"))
    ctx.ok(ctx.decide(alice, ia, "approve"))
    ta.join()
    tb.join()
    assert ba["result"][0].code == rp.ACCESS_ACCEPT and bb["result"][0].code == rp.ACCESS_REJECT


def test_totp_challenge_and_replay(ctx):
    user = ctx.user("carol")
    issued = ctx.ok(ctx.api.post(f"/api/v1/users/{user['id']}/totp"))
    secret = issued["secret"]
    assert ctx.api.post(f"/api/v1/users/{user['id']}/totp/{issued['factor_id']}/confirm", json={"code": "000000"}).status_code == 400
    # подтверждение занимает текущий шаг; вход ждёт следующего, иначе код сочтётся повтором
    ctx.ok(ctx.api.post(f"/api/v1/users/{user['id']}/totp/{issued['factor_id']}/confirm", json={"code": totp.code_at(secret)}))
    time.sleep(30 - time.time() % 30 + 0.5)
    login = f"carol-{RUN}"
    pkt, _ = radius(login, "Correct-Horse-Battery-1", ctx.radius_secret)
    assert pkt.code == rp.ACCESS_CHALLENGE and len(pkt.get(rp.STATE)) == 16
    code = totp.code_at(secret)
    ok, _ = radius(login, code, ctx.radius_secret, state=pkt.get(rp.STATE))
    assert ok.code == rp.ACCESS_ACCEPT
    # тот же State повторно — транзакция закрыта
    again, _ = radius(login, code, ctx.radius_secret, state=pkt.get(rp.STATE))
    assert again.code == rp.ACCESS_REJECT
    # тот же код в новой транзакции — повтор, отказ
    pkt2, _ = radius(login, "Correct-Horse-Battery-1", ctx.radius_secret)
    replay, _ = radius(login, code, ctx.radius_secret, state=pkt2.get(rp.STATE))
    assert replay.code == rp.ACCESS_REJECT
    # чужой State не принимается
    pkt3, _ = radius(login, "Correct-Horse-Battery-1", ctx.radius_secret)
    forged, _ = radius(f"alice-{RUN}", code, ctx.radius_secret, state=pkt3.get(rp.STATE))
    assert forged.code == rp.ACCESS_REJECT


def test_rest_access_request_with_push(ctx):
    phone = ctx.phones["alice"]
    created = ctx.ok(ctx.api.post("/api/v1/access-requests", json={"login": f"alice-{RUN}", "password": "Correct-Horse-Battery-1",
                                                                     "resource_id": ctx.resource["id"], "source_ip": "198.51.100.30"}))
    assert created["status"] == "pending"
    item = ctx.pending(phone)[0]
    ctx.ok(ctx.decide(phone, item, "approve"))
    assert ctx.ok(ctx.api.get(f"/api/v1/access-requests/{created['id']}", params={"wait": 5}))["status"] == "accepted"
    # другой API-клиент не видит чужую транзакцию
    other = ctx.ok(ctx.api.post("/api/v1/api-clients", json={"name": f"other-{RUN}", "scopes": ["auth:write"]}), 201)
    foreign = httpx.get(f"{URL}/api/v1/access-requests/{created['id']}", auth=(other["key_id"], other["secret"]))
    assert foreign.status_code == 404
    narrow = httpx.get(f"{URL}/api/v1/users", auth=(other["key_id"], other["secret"]))
    assert narrow.status_code == 403


def test_policy_deny_and_group_exception(ctx):
    group = ctx.ok(ctx.api.post("/api/v1/groups", json={"name": f"contractors-{RUN}"}), 201)
    ctx.ok(ctx.api.patch(f"/api/v1/users/{ctx.users['eve']['id']}", json={"group_ids": [group["id"]]}))
    deny = ctx.ok(ctx.api.post("/api/v1/policies", json={"name": f"deny {RUN}", "priority": 1, "resource_ids": [ctx.resource["id"]],
                                                         "groups": [group["name"]], "action": "deny"}), 201)
    try:
        pkt, _ = radius(f"eve-{RUN}", "Correct-Horse-Battery-1", ctx.radius_secret)
        assert pkt.code == rp.ACCESS_REJECT and "политик" in (pkt.text(rp.REPLY_MESSAGE) or "")
        ctx.ok(ctx.api.put(f"/api/v1/policies/{deny['id']}", json={"name": f"allow {RUN}", "priority": 1, "resource_ids": [ctx.resource["id"]],
                                                                  "groups": [group["name"]], "action": "allow"}))
        pkt, _ = radius(f"eve-{RUN}", "Correct-Horse-Battery-1", ctx.radius_secret)
        assert pkt.code == rp.ACCESS_ACCEPT
    finally:
        ctx.api.delete(f"/api/v1/policies/{deny['id']}")


def test_lockout_after_failures(ctx):
    user = ctx.user("mallory")
    for _ in range(8):
        radius(f"mallory-{RUN}", "wrong", ctx.radius_secret)
    pkt, _ = radius(f"mallory-{RUN}", "Correct-Horse-Battery-1", ctx.radius_secret)
    assert pkt.code == rp.ACCESS_REJECT, "верный пароль во время блокировки не пускает"
    locked = ctx.ok(ctx.api.get("/api/v1/auth-requests", params={"login": f"mallory-{RUN}", "reason": "locked"}))
    assert locked["total"] >= 1, "точная причина остаётся в истории для администратора"
    ctx.ok(ctx.api.post(f"/api/v1/users/{user['id']}/unlock"))


def test_revoked_device_cannot_poll(ctx):
    ctx.user("dave")
    phone = ctx.enroll("dave")
    ctx.ok(ctx.api.post(f"/api/v1/devices/{phone.device_id}/revoke"))
    resp = ctx.dev.get("/api/device/v1/ping", headers=phone.auth)
    assert resp.status_code == 401 and resp.json()["code"] == "device_revoked"


def test_rest_access_request_rejected_returns_result(ctx):
    """Отказ по REST API — обычный ответ со статусом rejected, а не ошибка сервера."""
    resp = ctx.api.post("/api/v1/access-requests", json={"login": f"alice-{RUN}", "password": "wrong-password", "resource_id": ctx.resource["id"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rejected" and resp.json()["id"]


def test_resource_bound_key_cannot_switch_resource(ctx):
    other = ctx.ok(ctx.api.post("/api/v1/resources", json={"name": f"Other {RUN}"}), 201)
    key = ctx.ok(ctx.api.post("/api/v1/api-clients", json={"name": f"bound-{RUN}", "scopes": ["auth:write"],
                                                         "resource_id": ctx.resource["id"]}), 201)
    resp = httpx.post(f"{URL}/api/v1/access-requests", auth=(key["key_id"], key["secret"]),
                      json={"login": f"alice-{RUN}", "password": "x", "resource_id": other["id"]})
    assert resp.status_code == 403


def test_radius_client_without_resource_creates_it(ctx):
    """Первый шлюз — одним действием: ресурс VPN с именем клиента создаётся сам, при изменении без ресурса прежний сохраняется."""
    name = f"auto-gw-{RUN}"
    rc = ctx.ok(ctx.api.post("/api/v1/radius-clients", json={"name": name, "address": "198.51.100.77/32"}), 201)
    try:
        res = next(r for r in ctx.ok(ctx.api.get("/api/v1/resources"))["items"] if r["id"] == rc["resource_id"])
        assert res["name"] == name and res["kind"] == "vpn"
        upd = ctx.ok(ctx.api.put(f"/api/v1/radius-clients/{rc['id']}", json={"name": name, "address": "198.51.100.77/32", "enabled": False}))
        assert upd["resource_id"] == res["id"]
    finally:
        ctx.api.delete(f"/api/v1/radius-clients/{rc['id']}")


def test_api_key_cannot_grant_admin_roles(ctx):
    resp = ctx.api.post("/api/v1/users", json={"login": f"root-{RUN}", "password": "Correct-Horse-Battery-1", "admin_role": "superadmin"})
    assert resp.status_code == 403


@pytest.mark.skipif(not os.environ.get("MFA_GATE_TEST_ADMIN"), reason="нет MFA_GATE_TEST_ADMIN (login:password нового администратора без TOTP)")
def test_admin_login_requires_second_factor(ctx):
    admin_login, admin_password = os.environ["MFA_GATE_TEST_ADMIN"].split(":", 1)
    web = httpx.Client(base_url=URL, timeout=20)
    assert web.post("/api/v1/auth/login", json={"login": admin_login, "password": "wrong-password"}).status_code == 401
    login = web.post("/api/v1/auth/login", json={"login": admin_login, "password": admin_password})
    assert login.status_code == 200
    if login.json()["next"] == "password_change":   # администратор, созданный установщиком: временный пароль
        assert web.post("/api/v1/auth/totp-setup").status_code == 403, "до смены пароля второй фактор недоступен"
        bad = web.post("/api/v1/auth/password", json={"current_password": "wrong", "new_password": "Brand-New-Password-1"})
        assert bad.status_code == 400
        changed = web.post("/api/v1/auth/password", json={"current_password": admin_password, "new_password": "Brand-New-Password-1"})
        assert changed.status_code == 200 and changed.json()["next"] == "totp_setup", changed.text
        login = changed
    assert login.json()["next"] == "totp_setup"
    assert web.get("/api/v1/users").status_code == 401, "до второго фактора данные недоступны"
    setup = web.post("/api/v1/auth/totp-setup").json()
    me = web.post(f"/api/v1/auth/totp-setup/{setup['factor_id']}", json={"code": totp.code_at(setup["secret"])}).json()
    assert me["mfa_passed"] and me["role"] == "superadmin"
    assert web.get("/api/v1/users").status_code == 200
    assert web.post("/api/v1/groups", json={"name": f"x-{RUN}"}).status_code == 403, "без CSRF-заголовка изменения запрещены"
    csrf = {"X-CSRF-Token": me["csrf"]}
    assert web.post("/api/v1/groups", json={"name": f"x-{RUN}"}, headers=csrf).status_code == 201

    # ключ API (права users/devices, но не admins) не может трогать учётные записи администраторов
    root_id = ctx.ok(ctx.api.get("/api/v1/users", params={"q": admin_login}))["items"][0]["id"]
    assert ctx.api.patch(f"/api/v1/users/{root_id}", json={"password": "Hijacked-Password-1"}).status_code == 403
    assert ctx.api.post(f"/api/v1/users/{root_id}/enrollment").status_code == 403
    assert ctx.api.post(f"/api/v1/users/{root_id}/totp").status_code == 403

    # второй администратор входит только телефоном: подмена фактора через totp-setup запрещена, push-одобрение одноразовое
    second = web.post("/api/v1/users", headers=csrf, json={"login": f"ops-{RUN}", "password": "Correct-Horse-Battery-1",
                                                          "admin_role": "admin"})
    assert second.status_code == 201, second.text
    ctx.users["ops"] = second.json()
    issued = web.post(f"/api/v1/users/{second.json()['id']}/enrollment", headers=csrf).json()
    q = parse_qs(urlparse(issued["uri"]).query)
    key = ec.generate_private_key(ec.SECP256R1())
    spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    dev = ctx.ok(ctx.dev.post("/api/device/v1/enroll", json={"token": q["token"][0], "device_name": "ops", "public_key": b64u(spki)}))
    phone = Phone(dev["device_id"], dev["device_secret"], key)

    attacker = httpx.Client(base_url=URL, timeout=20)
    step1 = attacker.post("/api/v1/auth/login", json={"login": f"ops-{RUN}", "password": "Correct-Horse-Battery-1"}).json()
    assert step1["next"] == "mfa" and step1["methods"] == ["push"]
    assert attacker.post("/api/v1/auth/totp-setup").status_code == 409

    ops = httpx.Client(base_url=URL, timeout=40)
    ops.post("/api/v1/auth/login", json={"login": f"ops-{RUN}", "password": "Correct-Horse-Battery-1"})
    started = ops.post("/api/v1/auth/mfa/push").json()
    item = ctx.pending(phone)[0]
    assert item["resource"] == "Панель управления MFA Gate"
    ctx.ok(ctx.decide(phone, item, "approve"))
    assert ops.get(f"/api/v1/auth/mfa/push/{started['request_id']}", params={"wait": 5}).json()["status"] == "approved"
    assert ops.get("/api/v1/users").status_code == 200
    # чужая сессия (тот же пароль) и повтор не используют одобрение
    assert attacker.get(f"/api/v1/auth/mfa/push/{started['request_id']}").status_code == 404
    assert ops.get(f"/api/v1/auth/mfa/push/{started['request_id']}").json()["status"] == "consumed"

    # повторный запрос push той же сессией не плодит уведомления, пока открыт прежний
    again = httpx.Client(base_url=URL, timeout=20)
    again.post("/api/v1/auth/login", json={"login": f"ops-{RUN}", "password": "Correct-Horse-Battery-1"})
    first, second = again.post("/api/v1/auth/mfa/push").json(), again.post("/api/v1/auth/mfa/push").json()
    assert first["request_id"] == second["request_id"]
    # отвязка телефона по device_secret + пароль не открывают подмену второго фактора
    assert ctx.dev.delete("/api/device/v1/self", headers=phone.auth).status_code == 200
    thief = httpx.Client(base_url=URL, timeout=20)
    assert thief.post("/api/v1/auth/login", json={"login": f"ops-{RUN}", "password": "Correct-Horse-Battery-1"}).json()["next"] == "totp_setup"
    assert thief.post("/api/v1/auth/totp-setup").status_code == 409


def test_diagnostics_and_audit(ctx):
    diag = ctx.ok(ctx.api.get("/api/v1/system/diagnostics"))
    names = {c["name"]: c["status"] for c in diag["checks"]}
    assert names["База данных"] == "ok" and names["Миграции"] == "ok" and names["RADIUS-сервер"] == "ok"
    events = ctx.ok(ctx.api.get("/api/v1/audit", params={"size": 200}))["items"]
    text = str(events)
    assert "Correct-Horse-Battery-1" not in text and ctx.radius_secret not in text, "секреты не должны попадать в журнал"


def test_long_polls_do_not_exhaust_db_pool(ctx):
    """40 телефонов с открытым приложением (long-poll) не должны занимать соединения БД и блокировать вход."""
    phone = ctx.phones["bob"]
    threads = [in_thread(lambda: httpx.get(f"{URL}/api/device/v1/requests", params={"status": "pending", "wait": 8}, headers=phone.auth,
                                           timeout=20).status_code)[0] for _ in range(40)]
    time.sleep(1)
    started = time.monotonic()
    ctx.ok(ctx.api.get("/api/v1/dashboard"))
    pkt, _ = radius(f"nobody-{RUN}", "x", ctx.radius_secret)
    assert pkt is not None and time.monotonic() - started < 3
    for t in threads:
        t.join()


def test_login_rate_limit_is_shared_between_api_instances(ctx):
    """Лимит попыток входа (20 в минуту с адреса) хранится в PostgreSQL: параллельные запросы к двум экземплярам api
    в сумме пропускают ровно 20. Второй экземпляр — MFA_GATE_TEST_URL2 (если не задан, оба потока идут в один)."""
    urls = [URL, os.environ.get("MFA_GATE_TEST_URL2") or URL]
    client_ip = f"203.0.113.{secrets.randbelow(200) + 20}"   # отдельный «адрес клиента» через X-Forwarded-For от доверенного прокси
    codes: list[int] = []
    lock = threading.Lock()

    def attempt(i: int) -> None:
        r = httpx.post(f"{urls[i % 2]}/api/v1/auth/login", json={"login": f"nobody-{RUN}", "password": "x"},
                       headers={"X-Forwarded-For": client_ip}, timeout=30)
        with lock:
            codes.append(r.status_code)
    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert codes.count(401) == 20 and codes.count(429) == 10, sorted(codes)


def test_openvpn_script_deferred_auth(ctx, tmp_path):
    """integrations/openvpn/mfa-gate-openvpn.sh: отложенная проверка OpenVPN через REST API (push → 1, неверный TOTP → 0)."""
    import base64 as b64
    import subprocess
    script = Path(__file__).resolve().parents[2] / "integrations" / "openvpn" / "mfa-gate-openvpn.sh"
    key = ctx.ok(ctx.api.post("/api/v1/api-clients", json={"name": f"openvpn-{RUN}", "scopes": ["auth:write"],
                                                         "resource_id": ctx.resource["id"]}), 201)
    conf = tmp_path / "mfa-gate.conf"
    conf.write_text(f"MFA_GATE_URL={URL}\nMFA_GATE_KEY={key['key_id']}:{key['secret']}\nMFA_GATE_WAIT=20\n")

    def run(password: str) -> Path:
        creds, control = tmp_path / f"creds-{secrets.token_hex(2)}", tmp_path / f"control-{secrets.token_hex(2)}"
        creds.write_text(f"alice-{RUN}\n{password}\n")
        env = {**os.environ, "MFA_GATE_OPENVPN_CONF": str(conf), "auth_control_file": str(control), "untrusted_ip": "198.51.100.77",
               "common_name": "laptop"}
        assert subprocess.run([str(script), str(creds)], env=env, timeout=10).returncode == 2, "OpenVPN ждёт код 2 (отложенно)"
        return control

    control = run("Correct-Horse-Battery-1")
    item = ctx.pending(ctx.phones["alice"])[0]
    assert item["source_ip"] == "198.51.100.77"
    ctx.ok(ctx.decide(ctx.phones["alice"], item, "approve"))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not control.exists():
        time.sleep(0.3)
    assert control.read_text().strip() == "1"

    wrong = "SCRV1:" + b64.b64encode(b"Correct-Horse-Battery-1").decode() + ":" + b64.b64encode(b"000000").decode()
    control = run(wrong)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not control.exists():
        time.sleep(0.3)
    assert control.read_text().strip() == "0"


# ------------------------------------------------------------------------------------------- портал сотрудника
class Portal:
    """Браузер сотрудника: свой адрес через X-Forwarded-For (стенд доверяет шлюзу сети контейнера)."""

    def __init__(self, ip: str = "10.20.30.40") -> None:
        self.c = httpx.Client(base_url=URL, timeout=40, headers={"X-Forwarded-For": ip})
        self.csrf = ""

    def login(self, login: str, password: str = "Correct-Horse-Battery-1", status: int = 200) -> dict:
        r = self.c.post("/api/v1/portal/login", json={"login": login, "password": password})
        assert r.status_code == status, r.text
        data = r.json()
        self.csrf = data.get("csrf", "")
        return data

    def post(self, path: str, json: dict | None = None) -> httpx.Response:
        return self.c.post("/api/v1/portal" + path, json=json or {}, headers={"X-CSRF-Token": self.csrf})

    def get(self, path: str, **params) -> httpx.Response:
        return self.c.get("/api/v1/portal" + path, params=params)


def _enroll_by_uri(ctx: Ctx, uri: str, name: str) -> Phone:
    q = parse_qs(urlparse(uri).query)
    key = ec.generate_private_key(ec.SECP256R1())
    spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    data = ctx.ok(ctx.dev.post("/api/device/v1/enroll", json={"token": q["token"][0], "device_name": name, "public_key": b64u(spki)}))
    return Phone(data["device_id"], data["device_secret"], key)


def test_portal_first_enrollment_totp_and_backup_codes(ctx):
    ctx.user("pat")
    login = f"pat-{RUN}"
    p = Portal()
    assert p.login(login, "wrong", status=401)["message"]
    st = p.login(login)
    assert st["stage"] == "enroll" and "totp" in st["methods"] and st["factors"]["totp"] == []
    assert p.get("/history").status_code == 401, "до второго фактора кабинет закрыт"
    assert p.c.post("/api/v1/portal/totp").status_code == 403, "без CSRF изменения запрещены"
    setup = ctx.ok(p.post("/totp"))
    assert p.post(f"/totp/{setup['factor_id']}/confirm", {"code": "000000"}).status_code == 400
    st = ctx.ok(p.post(f"/totp/{setup['factor_id']}/confirm", {"code": totp.code_at(setup["secret"])}))
    assert st["stage"] == "ready" and len(st["factors"]["totp"]) == 1
    assert p.c.delete(f"/api/v1/portal/totp/{st['factors']['totp'][0]['id']}", headers={"X-CSRF-Token": p.csrf}).status_code == 409, \
        "последний фактор удалить нельзя"
    assert [s for s in ctx.ok(p.get("/sessions"))["items"] if s["current"]]
    codes = ctx.ok(p.post("/backup-codes"))["codes"]
    assert len(codes) == 10 and len(set(codes)) == 10

    # сессия портала не открывает панель управления, даже если подставить её токен в cookie панели
    token = p.c.cookies.get("mfa_gate_portal")
    assert httpx.get(f"{URL}/api/v1/auth/me", cookies={"mfa_gate_session": token}).status_code == 401

    # второй вход: фактор уже есть → только второй фактор; резервный код одноразовый
    q = Portal()
    st = q.login(login)
    assert st["stage"] == "mfa" and set(st["login_methods"]) == {"totp", "backup"}
    assert q.post("/totp").status_code == 403, "добавлять факторы по одному паролю нельзя"
    assert ctx.ok(q.post("/mfa", {"code": codes[0].upper()}))["stage"] == "ready"
    r = Portal()
    r.login(login)
    assert r.post("/mfa", {"code": codes[0]}).status_code == 401
    assert ctx.ok(r.post("/mfa", {"code": codes[1]}))["factors"]["backup_codes_left"] == 8
    # чужой сеанс завершается из кабинета
    other = [s for s in ctx.ok(r.get("/sessions"))["items"] if not s["current"]][0]
    ctx.ok(r.c.delete(f"/api/v1/portal/sessions/{other['id']}", headers={"X-CSRF-Token": r.csrf}))
    assert r.c.delete("/api/v1/portal/sessions/%25%25%25%25%25%25%25%25%25%25%25%25%25%25%25%25",
                      headers={"X-CSRF-Token": r.csrf}).status_code == 404


def test_portal_enrollment_rules_phone_and_recovery(ctx):
    user = ctx.user("ron")
    login = f"ron-{RUN}"
    outside = Portal("203.0.113.9")
    st = outside.login(login)
    assert st["stage"] == "enroll_denied" and st["enroll_denied"] == "network"
    assert outside.post("/devices/enrollment").status_code == 403

    p = Portal()
    assert p.login(login)["stage"] == "enroll"
    stale = ctx.ok(p.post("/devices/enrollment"))["uri"]   # вторая ссылка той же сессии «только пароль»
    phone = _enroll_by_uri(ctx, ctx.ok(p.post("/devices/enrollment"))["uri"], "ron-phone")
    q = parse_qs(urlparse(stale).query)
    spki = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    assert ctx.dev.post("/api/device/v1/enroll", json={"token": q["token"][0], "device_name": "late", "public_key": b64u(spki)}).status_code == 410, \
        "после подтверждения фактора прежние ссылки привязки сотрудника гаснут"
    st = ctx.ok(p.get("/me"))
    assert st["stage"] == "mfa" and st["login_methods"] == ["push"], "после привязки — подтверждение на телефоне"
    started = ctx.ok(p.post("/mfa/push"))
    item = ctx.pending(phone)[0]
    assert item["resource"] == "Личный кабинет MFA Gate"
    ctx.ok(ctx.decide(phone, item, "approve"))
    assert ctx.ok(p.get(f"/mfa/push/{started['request_id']}", wait=5))["status"] == "approved"
    assert ctx.ok(p.get("/me"))["stage"] == "ready"

    # телефон потерян: администратор отзывает его — заново по одному паролю нельзя, нужна его санкция
    ctx.ok(ctx.api.post(f"/api/v1/devices/{phone.device_id}/revoke"))
    assert Portal().login(login)["enroll_denied"] == "reenroll"
    ctx.ok(ctx.api.post(f"/api/v1/users/{user['id']}/reenroll"))
    assert ctx.ok(ctx.api.get(f"/api/v1/users/{user['id']}"))["reenroll_until"]
    assert Portal("203.0.113.9").login(login)["enroll_denied"] == "network", "разрешение не отменяет ограничение по сетям"
    again = Portal()
    assert again.login(login)["stage"] == "enroll"
    setup = ctx.ok(again.post("/totp"))
    ctx.ok(again.post(f"/totp/{setup['factor_id']}/confirm", {"code": totp.code_at(setup["secret"])}))
    assert ctx.ok(ctx.api.get(f"/api/v1/users/{user['id']}"))["reenroll_until"] is None, "разрешение гаснет после настройки"


def test_radius_gateway_first_factor(ctx):
    """Шлюз сам проверил пароль (режимы «только логин» / «логин и код»): пустой пароль — push, цифры — OTP, иное — не пароль."""
    gil = ctx.user("gil")
    phone = ctx.enroll("gil")
    gwt = ctx.user("gwt")
    issued = ctx.ok(ctx.api.post(f"/api/v1/users/{gwt['id']}/totp"))
    ctx.ok(ctx.api.post(f"/api/v1/users/{gwt['id']}/totp/{issued['factor_id']}/confirm", json={"code": totp.code_at(issued["secret"])}))
    rc = ctx.radius_client
    body = {k: rc[k] for k in ("resource_id", "name", "address", "nas_identifier", "enabled")}
    ctx.ok(ctx.api.put(f"/api/v1/radius-clients/{rc['id']}", json={**body, "first_factor": "gateway"}))
    try:
        deadline = time.monotonic() + 40   # RADIUS перечитывает клиентов раз в 10–15 с
        while time.monotonic() < deadline:
            # пароль «не код» в режиме шлюза игнорируется → запрос кода; в режиме mfa_gate это неверный пароль
            probe, _ = radius(gwt["login"], "domain-password-checked-by-gateway", ctx.radius_secret, timeout=3)
            if probe is not None and probe.code == rp.ACCESS_CHALLENGE:
                break
            time.sleep(3)
        else:
            pytest.fail("RADIUS не подхватил режим gateway за 40 с")
        # TOTP-пользователь: Access-Challenge и ответ кодом (режим «Логин»); «Логин и OTP-код» — код сразу
        time.sleep(30 - time.time() % 30 + 0.5)
        ok, _ = radius(gwt["login"], totp.code_at(issued["secret"]), ctx.radius_secret)
        assert ok.code == rp.ACCESS_ACCEPT
        bad, _ = radius(gwt["login"], "000000", ctx.radius_secret)
        assert bad.code == rp.ACCESS_REJECT
        # push-пользователь: пустой пароль, запрос держится до решения на телефоне
        t, box = in_thread(radius, gil["login"], "", ctx.radius_secret, timeout=PUSH_TIMEOUT + 10)
        ctx.ok(ctx.decide(phone, ctx.pending(phone)[0], "approve"))
        t.join()
        assert box["result"][0].code == rp.ACCESS_ACCEPT
    finally:
        ctx.ok(ctx.api.put(f"/api/v1/radius-clients/{rc['id']}", json={**body, "first_factor": "mfa_gate"}))
        time.sleep(16)   # остальные тесты рассчитывают на проверку пароля MFA Gate


def test_portal_and_admin_addresses(ctx):
    assert "личный кабинет" in httpx.get(f"{URL}/").text
    assert "панель управления" in httpx.get(f"{URL}/admin/").text
    assert httpx.get(f"{URL}/admin").status_code == 308
    pub = ctx.ok(ctx.api.get("/api/v1/system/publication"))
    assert {c["id"] for c in pub["components"]} >= {"portal", "admin", "device", "radius"} and "/api/device/" in pub["nginx"]
    host = os.environ.get("MFA_GATE_TEST_DEVICE_HOST")
    if not host:
        pytest.skip("отдельный адрес Device API на стенде не задан")
    assert httpx.get(f"{URL}/", headers={"Host": host}).status_code == 404, "внешний адрес телефонов не отдаёт портал"
    assert httpx.get(f"{URL}/api/v1/portal/me", headers={"Host": host}).status_code == 404
    assert httpx.get(f"{URL}/api/device/v1/ping", headers={"Host": host}).status_code == 401, "Device API доступен"
