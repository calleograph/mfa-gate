"""Модуль каталога: юнит-тесты без сервера и тесты на настоящем OpenLDAP (метка ldap, поднимает tests/ldap_fixture/run.sh).

Все данные вымышленные: example.org, 192.0.2.x (TEST-NET-1)."""
from __future__ import annotations

import base64
import os
import re
import secrets
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from mfa_gate.config import settings
from mfa_gate.models import LdapDirectory
from mfa_gate.security import crypto
from mfa_gate.services import ldap as L

URL = os.environ.get("MFA_GATE_TEST_LDAP_URL", "")
_u = urlsplit(URL) if URL else None
HOST = _u.hostname if _u else "192.0.2.10"
PORT = (_u.port or 389) if _u else 389
LDAPS_PORT = int(os.environ.get("MFA_GATE_TEST_LDAPS_PORT") or 636)
BASE_DN = os.environ.get("MFA_GATE_TEST_LDAP_BASE_DN", "dc=example,dc=org")
BIND_DN = os.environ.get("MFA_GATE_TEST_LDAP_BIND_DN", "cn=readonly,dc=example,dc=org")
BIND_PW = os.environ.get("MFA_GATE_TEST_LDAP_BIND_PASSWORD", "unit-test-bind-secret")
USER_PW = os.environ.get("MFA_GATE_TEST_LDAP_USER_PASSWORD", "")
CA = Path(os.environ["MFA_GATE_TEST_LDAP_CA"]).read_text() if os.environ.get("MFA_GATE_TEST_LDAP_CA") else ""
WRONG_CA = Path(os.environ["MFA_GATE_TEST_LDAP_WRONG_CA"]).read_text() if os.environ.get("MFA_GATE_TEST_LDAP_WRONG_CA") else ""
DEAD = "192.0.2.1"   # TEST-NET-1: не маршрутизируется, соединение не устанавливается

real = pytest.mark.skipif(not URL, reason="MFA_GATE_TEST_LDAP_URL не задан: запустите tests/ldap_fixture/run.sh")
GROUP = "cn={},ou=groups,dc=example,dc=org"


@pytest.fixture(autouse=True)
def master_key(monkeypatch):
    monkeypatch.setenv("MFA_GATE_MASTER_KEY", base64.b64encode(secrets.token_bytes(32)).decode())
    settings.cache_clear()
    crypto.box.cache_clear()
    yield
    settings.cache_clear()
    crypto.box.cache_clear()


def make_dir(bind_password: str | None = None, **kw) -> LdapDirectory:
    d = LdapDirectory(id=uuid.uuid4(), name="test", kind="openldap", hosts=[HOST], port=PORT, encryption="none", verify_tls=True, ca_cert=None,
                      bind_dn=BIND_DN, base_dn=BASE_DN, user_filter="(objectClass=inetOrgPerson)", login_attribute="uid", id_attribute="entryUUID",
                      name_attribute="displayName", email_attribute="mail", phone_attribute="mobile", nested_groups=True, allowed_groups=[], timeout=3)
    for k, v in kw.items():
        setattr(d, k, v)
    pw = BIND_PW if bind_password is None else bind_password
    d.bind_password_enc = crypto.box().encrypt(pw, aad=b"ldap:" + str(d.id).encode()) if d.bind_dn and pw else None
    return d


# ------------------------------------------------------------------------------------------------ без сервера
@pytest.mark.parametrize(("message", "code"), [
    ("80090308: LdapErr: DSID-0C09044E, comment: AcceptSecurityContext error, data 52e, v4563", "invalid_credentials"),
    ("80090308: LdapErr: DSID-0C09044E, comment: AcceptSecurityContext error, data 775, v4563", "account_locked"),
    ("80090308: LdapErr: DSID-0C09044E, comment: AcceptSecurityContext error, data 533, v4563", "account_disabled"),
    ("80090308: LdapErr: DSID-0C09044E, comment: AcceptSecurityContext error, data 701, v4563", "account_disabled"),
    ("80090308: LdapErr: DSID-0C09044E, comment: AcceptSecurityContext error, data 530, v4563", "account_disabled"),
    ("80090308: LdapErr: DSID-0C09044E, comment: AcceptSecurityContext error, data 532, v4563", "password_expired"),
    ("80090308: LdapErr: DSID-0C09044E, comment: AcceptSecurityContext error, data 773, v4563", "must_change_password"),
    ("80090308: LdapErr: DSID-0C09044E, comment: AcceptSecurityContext error, data 525, v4563", "not_found"),
    ("", "invalid_credentials"),                                    # OpenLDAP: без подкода
])
def test_classify_ad_subcodes(message, code):
    assert L.classify_bind({"result": 49, "description": "invalidCredentials", "message": message}) == code


def test_classify_other_results_and_detail():
    assert L.classify_bind({"result": 52, "description": "unavailable"}) == "unavailable"
    assert L.classify_bind({"result": 81}) == "unavailable"
    assert L.classify_bind({"result": 8, "description": "strongerAuthRequired"}) == "tls_error"
    assert L.classify_bind(None) == "invalid_credentials"
    detail = L.bind_detail({"result": 49, "description": "invalidCredentials", "message": "AcceptSecurityContext error, data 775, v4563"})
    assert detail == "LDAP 49 invalidCredentials; AD 775: учётная запись заблокирована"


@pytest.mark.parametrize(("raw", "expected"), [
    ("alice", ("alice", None)),
    ("  alice  ", ("alice", None)),
    ("CORP\\alice", ("alice", None)),
    ("alice@corp.example", ("alice", "alice@corp.example")),
    ("CORP\\alice@corp.example", ("alice", "alice@corp.example")),
])
def test_parse_login(raw, expected):
    assert L.parse_login(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "CORP\\", "\\alice", "a\\b\\c", "@corp.example", "alice@", "al\x00ice", "x" * 300])
def test_parse_login_rejects(raw):
    with pytest.raises(L.LdapError) as e:
        L.parse_login(raw)
    assert e.value.code == "not_found"


def test_object_guid_to_uuid():
    raw = bytes.fromhex("e004253f894fd3119a0c0305e82c3301")     # порядок байтов AD (первые три поля little-endian)
    assert L.guid_to_str(raw) == "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
    cfg = L._snapshot(make_dir(kind="ad", id_attribute="objectGUID", bind_dn=""))
    assert L.external_id(cfg, {"dn": "CN=a", "raw_attributes": {"objectGUID": [raw]}}) == "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
    with pytest.raises(L.LdapError) as e:
        L.external_id(cfg, {"dn": "CN=a", "raw_attributes": {}})
    assert e.value.code == "config_error"
    cfg2 = L._snapshot(make_dir(bind_dn=""))
    assert L.external_id(cfg2, {"dn": "uid=a", "attributes": {"entryUUID": ["0b6c3a1e-0000-4000-8000-000000000001"]}}) == "0b6c3a1e-0000-4000-8000-000000000001"


def test_user_filter_escapes_and_upn():
    cfg = L._snapshot(make_dir(bind_dn=""))
    assert L.user_filter(cfg, "*)(uid=*") == "(&(objectClass=inetOrgPerson)(uid=\\2a\\29\\28uid=\\2a))"
    ad = L._snapshot(make_dir(bind_dn="", kind="ad", user_filter="(&(objectClass=user)(objectCategory=person))", login_attribute="sAMAccountName"))
    flt = L.user_filter(ad, "alice@corp.example")
    assert "(sAMAccountName=alice)" in flt and "(userPrincipalName=alice@corp.example)" in flt
    assert L.user_filter(ad, "CORP\\alice") == "(&(&(objectClass=user)(objectCategory=person))(sAMAccountName=alice))"


def test_self_bind_name():
    ad = L._snapshot(make_dir(bind_dn="", kind="ad", base_dn="DC=corp,DC=example"))
    assert L._self_bind_name(ad, "alice") == "alice@corp.example"
    assert L._self_bind_name(ad, "CORP\\alice") == "CORP\\alice"
    assert L._self_bind_name(ad, "alice@corp.example") == "alice@corp.example"
    ol = L._snapshot(make_dir(bind_dn="", base_dn="ou=people,dc=example,dc=org"))
    assert L._self_bind_name(ol, "CORP\\alice") == "uid=alice,ou=people,dc=example,dc=org"


def test_group_allowed_cn_and_dn():
    groups = L._with_cn(["CN=VPN Users,OU=Groups,DC=corp,DC=example"])
    assert groups == ["CN=VPN Users,OU=Groups,DC=corp,DC=example", "VPN Users"]
    assert L.group_allowed(groups, [])
    assert L.group_allowed(groups, ["vpn users"])
    assert L.group_allowed(groups, ["cn=vpn users, ou=groups, dc=corp, dc=example"])
    assert not L.group_allowed(groups, ["Staff"])


@pytest.mark.parametrize(("change", "needle"), [
    ({"hosts": []}, "hosts"),
    ({"base_dn": ""}, "base_dn"),
    ({"encryption": "ssl"}, "шифрования"),
    ({"kind": "ldap"}, "тип"),
    ({"ca_cert": "-----BEGIN CERTIFICATE-----\nnot a cert\n-----END CERTIFICATE-----"}, "ca_cert"),
])
def test_snapshot_rejects_bad_config(change, needle):
    with pytest.raises(L.LdapError) as e:
        L._snapshot(make_dir(**change))
    assert e.value.code == "config_error" and needle in e.value.detail


def test_bind_dn_without_password_is_config_error():
    d = make_dir()
    d.bind_password_enc = None
    with pytest.raises(L.LdapError) as e:
        L._snapshot(d)
    assert e.value.code == "config_error"
    d = make_dir()
    d.id = uuid.uuid4()   # шифртекст привязан к id каталога (AAD) — в чужой записи не расшифруется
    with pytest.raises(L.LdapError) as e:
        L._snapshot(d)
    assert e.value.code == "config_error" and BIND_PW not in str(e.value)


async def test_empty_password_rejected_without_server(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("обращения к серверу быть не должно")

    monkeypatch.setattr(L, "_connect", boom)
    monkeypatch.setattr(L, "_service", boom)
    for pw in ("", None):
        with pytest.raises(L.LdapError) as e:
            await L.authenticate(make_dir(hosts=[DEAD]), "alice", pw)
        assert e.value.code == "invalid_credentials"


def test_unknown_error_code_rejected():
    with pytest.raises(ValueError):
        L.LdapError("oops")


class _FakeConn:
    """Ответы на поиск групп по member=…: граф членства задан словарём «группа → участники»."""

    def __init__(self, members: dict[str, list[str]], fail_paged: bool = False):
        self.members, self.fail_paged, self.response, self.result, self.searches = members, fail_paged, [], {"result": 0}, 0

    def search(self, base, flt, search_scope=None, attributes=None):
        self.searches += 1
        wanted = {v.lower() for v in re.findall(r"\(member=([^)]*)\)", flt)}
        self.response = [{"type": "searchResEntry", "dn": g} for g, ms in self.members.items() if wanted & {m.lower() for m in ms}]
        return True

    @property
    def extend(self):
        conn = self

        class _Std:
            def paged_search(self, *a, **k):
                if conn.fail_paged:
                    raise RuntimeError("inappropriateMatching")
                return iter(())

        class _Ext:
            standard = _Std()

        return _Ext()


def test_group_walk_depth_limit_and_cycles():
    chain = {f"cn=g{i},dc=example,dc=org": [f"cn=g{i - 1},dc=example,dc=org" if i else "uid=u,dc=example,dc=org"] for i in range(15)}
    chain["cn=g0,dc=example,dc=org"].append("cn=g3,dc=example,dc=org")   # цикл g0 → g3 → g0
    cfg = L._snapshot(make_dir(bind_dn=""))
    conn = _FakeConn(chain)
    groups = L._walk_groups(conn, cfg, "uid=u,dc=example,dc=org", [])
    assert groups == [f"cn=g{i},dc=example,dc=org" for i in range(L.MAX_GROUP_DEPTH)]
    assert conn.searches == L.MAX_GROUP_DEPTH
    flat = L._snapshot(make_dir(bind_dn="", nested_groups=False))
    assert L._walk_groups(_FakeConn(chain), flat, "uid=u,dc=example,dc=org", []) == ["cn=g0,dc=example,dc=org"]


def test_ad_nested_groups_failure_denies_login():
    """Неполный список групп мог бы обойти запрещающие политики: при сбое вложенного поиска — отказ, а не прямые группы."""
    cfg = L._snapshot(make_dir(bind_dn="", kind="ad"))
    entry = {"dn": "CN=Alice,DC=corp,DC=example", "attributes": {"memberOf": ["CN=Staff,DC=corp,DC=example"]}}
    with pytest.raises(L.LdapError) as err:
        L._groups(_FakeConn({}, fail_paged=True), cfg, entry)
    assert err.value.code == "unavailable"
    flat = L._snapshot(make_dir(bind_dn="", kind="ad", nested_groups=False))
    assert L._groups(_FakeConn({}, fail_paged=True), flat, entry) == ["CN=Staff,DC=corp,DC=example", "Staff"]


# ------------------------------------------------------------------------------------------------ настоящий OpenLDAP
def ldap_test(fn):
    return pytest.mark.ldap(real(fn))


@ldap_test
@pytest.mark.parametrize("login", ["alice", "EXAMPLE\\alice", "alice@example.org"])
async def test_login_success(login):
    user = await L.authenticate(make_dir(), login, USER_PW)
    assert user.dn == "uid=alice,ou=people,dc=example,dc=org"
    assert (user.login, user.display_name, user.email, user.phone) == ("alice", "Alice Example", "alice@example.org", "+1 555 0100")
    assert str(uuid.UUID(user.external_id)) == user.external_id      # entryUUID как есть


@ldap_test
async def test_wrong_password():
    with pytest.raises(L.LdapError) as e:
        await L.authenticate(make_dir(), "alice", "wrong-" + USER_PW)
    assert e.value.code == "invalid_credentials"
    assert USER_PW not in str(e.value) and BIND_PW not in str(e.value)


@ldap_test
async def test_unknown_user():
    with pytest.raises(L.LdapError) as e:
        await L.authenticate(make_dir(), "nobody", USER_PW)
    assert e.value.code == "not_found"


@ldap_test
async def test_empty_password_real():
    with pytest.raises(L.LdapError) as e:
        await L.authenticate(make_dir(), "alice", "")
    assert e.value.code == "invalid_credentials"


@ldap_test
async def test_nested_groups_and_cycle():
    groups = (await L.authenticate(make_dir(), "alice", USER_PW)).groups
    for cn in ("engineers", "vpn-users", "loop-a", "loop-b"):
        assert cn in groups and GROUP.format(cn) in groups
    assert "staff" not in groups
    flat = (await L.authenticate(make_dir(nested_groups=False), "alice", USER_PW)).groups
    assert "engineers" in flat and "loop-a" in flat and "vpn-users" not in flat


@ldap_test
async def test_allowed_groups():
    assert (await L.authenticate(make_dir(allowed_groups=["vpn-users"]), "alice", USER_PW)).login == "alice"
    assert (await L.authenticate(make_dir(allowed_groups=["CN=VPN-Users,OU=Groups,DC=example,DC=org"]), "alice", USER_PW)).login == "alice"
    with pytest.raises(L.LdapError) as e:
        await L.authenticate(make_dir(allowed_groups=["vpn-users"]), "bob", USER_PW)
    assert e.value.code == "not_allowed_group"


@ldap_test
async def test_service_account_wrong_password_is_config_error():
    with pytest.raises(L.LdapError) as e:
        await L.authenticate(make_dir(bind_password="not-the-password"), "alice", USER_PW)
    assert e.value.code == "config_error" and "not-the-password" not in str(e.value)


@ldap_test
async def test_bind_as_user_without_service_account():
    d = make_dir(bind_dn="", base_dn="ou=people,dc=example,dc=org")
    user = await L.authenticate(d, "alice", USER_PW)
    assert user.dn == "uid=alice,ou=people,dc=example,dc=org" and user.email == "alice@example.org"
    assert user.groups == []        # группы ищутся под base_dn, а здесь это ou=people
    with pytest.raises(L.LdapError) as e:
        await L.authenticate(d, "alice", "wrong-" + USER_PW)
    assert e.value.code == "invalid_credentials"
    with pytest.raises(L.LdapError) as e:
        await L.lookup(d, "alice")
    assert e.value.code == "config_error"


@ldap_test
async def test_lookup():
    user = await L.lookup(make_dir(), "alice")
    assert user is not None and "vpn-users" in user.groups
    assert await L.lookup(make_dir(), "nobody") is None


@ldap_test
async def test_unavailable_host_fails_fast():
    t = time.monotonic()
    with pytest.raises(L.LdapError) as e:
        await L.authenticate(make_dir(hosts=[DEAD], timeout=1), "alice", USER_PW)
    assert e.value.code == "unavailable" and time.monotonic() - t < 5
    with pytest.raises(L.LdapError) as e:                         # закрытый порт на живом узле
        await L.authenticate(make_dir(hosts=["127.0.0.1"], port=9, timeout=1), "alice", USER_PW)
    assert e.value.code == "unavailable"


@ldap_test
async def test_second_host_used_when_first_is_down():
    d = make_dir(hosts=[DEAD, HOST], timeout=2)
    assert (await L.authenticate(d, "alice", USER_PW)).login == "alice"
    t = time.monotonic()                                           # недоступный узел исключён из пула на 60 с — второй вход без ожидания
    assert (await L.authenticate(d, "alice", USER_PW)).login == "alice"
    assert time.monotonic() - t < 1.5


@ldap_test
async def test_ldaps_with_ca():
    if not CA:
        pytest.skip("MFA_GATE_TEST_LDAP_CA не задан: образ без TLS")
    base = {"hosts": ["localhost"], "port": LDAPS_PORT, "encryption": "ldaps"}
    with pytest.raises(L.LdapError) as e:
        await L.authenticate(make_dir(**base, ca_cert=WRONG_CA), "alice", USER_PW)
    assert e.value.code == "tls_error"
    with pytest.raises(L.LdapError) as e:                         # без ca_cert — системный набор, тестовый CA в нём не доверен
        await L.authenticate(make_dir(**base), "alice", USER_PW)
    assert e.value.code == "tls_error"
    assert (await L.authenticate(make_dir(**base, ca_cert=CA), "alice", USER_PW)).login == "alice"
    assert (await L.authenticate(make_dir(**base, verify_tls=False), "alice", USER_PW)).login == "alice"


@ldap_test
async def test_starttls_with_ca():
    if not CA:
        pytest.skip("MFA_GATE_TEST_LDAP_CA не задан: образ без TLS")
    base = {"hosts": ["localhost"], "encryption": "starttls"}
    with pytest.raises(L.LdapError) as e:
        await L.authenticate(make_dir(**base, ca_cert=WRONG_CA), "alice", USER_PW)
    assert e.value.code == "tls_error"
    assert (await L.authenticate(make_dir(**base, ca_cert=CA), "alice", USER_PW)).login == "alice"


@ldap_test
async def test_diagnose_steps():
    steps = await L.diagnose(make_dir(hosts=[DEAD, HOST], timeout=1))
    assert [(s["step"], s["host"], s["ok"]) for s in steps[:2]] == [("dns", DEAD, True), ("tcp", DEAD, False)]
    assert [(s["step"], s["ok"]) for s in steps[2:]] == [("dns", True), ("tcp", True), ("tls", True), ("bind", True), ("base_dn", True)]
    assert all(s["host"] == HOST for s in steps[2:]) and all(isinstance(s["ms"], int) for s in steps)

    full = await L.diagnose(make_dir(), "alice", USER_PW)
    assert [s["step"] for s in full] == ["dns", "tcp", "tls", "bind", "base_dn", "user_search", "user_bind", "groups"]
    assert all(s["ok"] for s in full) and "vpn-users" in full[-1]["detail"]

    bad = await L.diagnose(make_dir(), "alice", "wrong-" + USER_PW)
    assert bad[-1]["step"] == "user_bind" and not bad[-1]["ok"] and "invalid_credentials" in bad[-1]["detail"]
    missing = await L.diagnose(make_dir(), "nobody")
    assert missing[-1]["step"] == "user_search" and not missing[-1]["ok"]
    denied = await L.diagnose(make_dir(allowed_groups=["staff"]), "alice")
    assert denied[-1]["step"] == "groups" and not denied[-1]["ok"]
    svc = await L.diagnose(make_dir(bind_password="not-the-password"))
    assert svc[-1]["step"] == "bind" and not svc[-1]["ok"]
    for run in (steps, full, bad, svc):
        text = repr(run)
        assert USER_PW not in text and BIND_PW not in text and "not-the-password" not in text


@ldap_test
async def test_diagnose_tls_failure_per_host():
    if not CA:
        pytest.skip("MFA_GATE_TEST_LDAP_CA не задан: образ без TLS")
    steps = await L.diagnose(make_dir(hosts=["localhost"], port=LDAPS_PORT, encryption="ldaps", ca_cert=WRONG_CA))
    assert [(s["step"], s["ok"]) for s in steps] == [("dns", True), ("tcp", True), ("tls", False)]
    assert "certificate" in steps[-1]["detail"].lower()
    ok = await L.diagnose(make_dir(hosts=["localhost"], port=LDAPS_PORT, encryption="ldaps", ca_cert=CA), "alice", USER_PW)
    assert all(s["ok"] for s in ok) and ok[-1]["step"] == "groups"
