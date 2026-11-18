"""Каталог LDAP / Active Directory: проверка пароля bind'ом, поиск пользователя, группы с вложенностью, пошаговая диагностика.

* ldap3 блокирующий — каждая операция целиком выполняется в asyncio.to_thread. Настройки каталога снимаются в потоке событийного
  цикла (_snapshot): объект SQLAlchemy в рабочий поток не передаётся.
* Несколько контроллеров — ldap3 ServerPool(FIRST, exhaust=60): недоступный контроллер пропускается 60 с. Пул хранится на каталог.
* TLS: ldaps или starttls; при verify_tls сертификат проверяется (CERT_REQUIRED) по ca_cert, а без него — по системному набору CA,
  имя узла сверяется с сертификатом. encryption=none допускается настройкой, но пароль тогда идёт открытым текстом.
* Пустой пароль отклоняется до обращения к серверу (иначе это анонимный bind). Анонимный bind не выполняется никогда.
* Пароли (пользователя и сервисной записи) не логируются и не попадают в detail ошибок и шагов диагностики.
* Вложенные группы: AD — LDAP_MATCHING_RULE_IN_CHAIN с постраничным поиском и откатом к memberOf; OpenLDAP — обход member/uniqueMember
  вверх до глубины 10 с защитой от циклов.
Основа — донор A app/auth/ldap.py и донор B llmp/ldap/client.py, diagnostics.py."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import socket
import ssl
import threading
import time
import uuid
from dataclasses import dataclass, field

from ldap3 import (
    BASE,
    FIRST,
    NONE,
    SIMPLE,
    SUBTREE,
    Connection,
    Server,
    ServerPool,
    Tls,
)
from ldap3.core.exceptions import LDAPCertificateError, LDAPException, LDAPStartTLSError
from ldap3.utils.config import set_config_parameter
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import escape_rdn, parse_dn

log = logging.getLogger(__name__)

# ldap3 по умолчанию ждёт 10 с между проходами пула; у нас проход один (active=1), пауза только задерживала бы ответ «недоступен»
set_config_parameter("POOLING_LOOP_TIMEOUT", 0)

LDAP_MATCHING_RULE_IN_CHAIN = "1.2.840.113556.1.4.1941"
MAX_GROUP_DEPTH = 10
POOL_EXHAUST_SECONDS = 60
_NO_ATTRS = ["1.1"]
_UAC_DISABLED = 0x2
_GROUP_CLASSES = "(|(objectClass=group)(objectClass=groupOfNames)(objectClass=groupOfUniqueNames))"

CODES = frozenset({"invalid_credentials", "account_disabled", "account_locked", "password_expired", "must_change_password", "not_found",
                   "not_allowed_group", "unavailable", "tls_error", "config_error"})

# подкоды AD в diagnosticMessage ответа bind (код 49): «… data 52e, v4563»
AD_SUBCODES = {"525": "пользователь не найден", "52e": "неверный пароль", "52f": "ограничение учётной записи", "530": "вход запрещён в это время",
               "531": "вход запрещён с этого компьютера", "532": "срок действия пароля истёк", "533": "учётная запись отключена",
               "701": "срок действия учётной записи истёк", "773": "нужно сменить пароль", "775": "учётная запись заблокирована"}
_AD_CODE = {"525": "not_found", "52e": "invalid_credentials", "52f": "account_disabled", "530": "account_disabled", "531": "account_disabled",
            "532": "password_expired", "533": "account_disabled", "701": "account_disabled", "773": "must_change_password", "775": "account_locked"}


class LdapError(Exception):
    """code — машинная причина (CODES); detail — для журнала администратора, без паролей."""

    def __init__(self, code: str, detail: str = ""):
        if code not in CODES:
            raise ValueError(f"неизвестный код ошибки LDAP: {code}")
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


@dataclass
class DirectoryUser:
    dn: str
    external_id: str
    login: str
    display_name: str
    email: str | None
    phone: str | None
    groups: list[str]   # CN и DN групп (оба), включая вложенные


# ------------------------------------------------------------------------------------------------ чистые функции (юнит-тесты)
def parse_login(raw: str) -> tuple[str, str | None]:
    """(имя без домена, полное user@domain или None). Принимает sAMAccountName, UPN (user@domain), DOMAIN\\user."""
    s = (raw or "").strip()
    if not s or len(s) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in s):
        raise LdapError("not_found", "недопустимый логин")
    if "\\" in s:
        domain, _, s = s.partition("\\")
        if not domain or not s or "\\" in s:
            raise LdapError("not_found", "недопустимый логин")
    if "@" in s:
        local, _, domain = s.rpartition("@")
        if not local or not domain:
            raise LdapError("not_found", "недопустимый логин")
        return local, s
    return s, None


def guid_to_str(raw: bytes) -> str:
    """objectGUID AD (16 байт, первые три поля little-endian) → строка UUID, как её показывает AD."""
    return str(uuid.UUID(bytes_le=bytes(raw)))


def classify_bind(result: dict | None) -> str:
    """Ответ неудачного bind → код LdapError. Подкоды AD различают блокировку, отключение, истёкший пароль."""
    result = result or {}
    code = result.get("result")
    if code == 49:
        sub = re.search(r"data ([0-9a-f]{3})", str(result.get("message") or "").lower())
        return _AD_CODE.get(sub.group(1), "invalid_credentials") if sub else "invalid_credentials"
    if code in (51, 52, 81, 85):            # busy, unavailable, serverDown, timeout
        return "unavailable"
    if code in (8, 13):                     # strongerAuthRequired, confidentialityRequired — сервер требует шифрования
        return "tls_error"
    return "invalid_credentials"            # прочее при bind пользователя — отказ (fail closed), причина в detail


def bind_detail(result: dict | None) -> str:
    """«LDAP 49 invalidCredentials; AD 52e: неверный пароль». Текст diagnosticMessage целиком не пишется."""
    result = result or {}
    parts = [f"LDAP {result.get('result', '?')} {result.get('description', '')}".strip()]
    sub = re.search(r"data ([0-9a-f]{3})", str(result.get("message") or "").lower())
    if sub:
        parts.append(f"AD {sub.group(1)}: {AD_SUBCODES.get(sub.group(1), 'неизвестный подкод')}")
    return "; ".join(parts)


def _norm(name: str) -> str:
    # shortcut: запятые внутри экранированных значений DN не учитываются; хватает для сравнения DN групп из настроек
    return ",".join(p.strip() for p in name.split(",")).lower()


def group_allowed(groups: list[str], allowed: list[str]) -> bool:
    """Пусто — разрешены все. Иначе нужно пересечение по CN или DN без учёта регистра."""
    if not allowed:
        return True
    have = {_norm(g) for g in groups}
    return any(_norm(a) in have for a in allowed if a and a.strip())


def _cn(dn: str) -> str | None:
    try:
        attr, value, _sep = parse_dn(dn)[0]
    except Exception:  # noqa: BLE001 — нестандартный DN: остаётся только сам DN
        return None
    return re.sub(r"\\(.)", r"\1", value) if attr.lower() == "cn" else None


def _with_cn(dns: list[str]) -> list[str]:
    out, seen = [], set()
    for dn in dns:
        for name in (dn, _cn(dn)):
            if name and name.lower() not in seen:
                seen.add(name.lower())
                out.append(name)
    return out


# ------------------------------------------------------------------------------------------------ настройки
@dataclass(frozen=True)
class _Cfg:
    id: str
    kind: str
    hosts: tuple[str, ...]
    port: int
    encryption: str
    verify_tls: bool
    ca_cert: str
    bind_dn: str
    bind_password: str = field(repr=False)
    base_dn: str = ""
    user_filter: str = ""
    login_attr: str = "sAMAccountName"
    id_attr: str = "objectGUID"
    name_attr: str = "displayName"
    email_attr: str = "mail"
    phone_attr: str = "mobile"
    nested: bool = True
    allowed: tuple[str, ...] = ()
    timeout: int = 5


def _snapshot(d) -> _Cfg:
    """Читает поля LdapDirectory (в потоке событийного цикла), проверяет и расшифровывает пароль сервисной записи."""
    hosts = tuple(str(h).strip() for h in (d.hosts or []) if str(h).strip())
    kind, enc = (d.kind or "ad"), (d.encryption or "ldaps")
    problems = [msg for bad, msg in ((not hosts, "не заданы контроллеры (hosts)"), (not (d.base_dn or "").strip(), "не задан base_dn"),
                                     (kind not in ("ad", "openldap"), f"неизвестный тип каталога «{kind}»"),
                                     (enc not in ("ldaps", "starttls", "none"), f"неизвестный режим шифрования «{enc}»")) if bad]
    if problems:
        raise LdapError("config_error", "; ".join(problems))
    ca = (d.ca_cert or "").strip()
    if ca:
        try:
            ssl.create_default_context(cadata=ca)
        except (ssl.SSLError, ValueError):
            raise LdapError("config_error", "ca_cert не является сертификатом в формате PEM") from None
    bind_dn, password = (d.bind_dn or "").strip(), ""
    if bind_dn:
        if not d.bind_password_enc:
            raise LdapError("config_error", "задан bind_dn, но нет пароля сервисной учётной записи (анонимный bind не выполняется)")
        from ..security.crypto import box  # noqa: PLC0415

        try:
            password = box().decrypt(d.bind_password_enc, aad=b"ldap:" + str(d.id).encode())
        except Exception:  # noqa: BLE001
            raise LdapError("config_error", "пароль сервисной учётной записи не расшифровывается (другой мастер-ключ или повреждён)") from None
        if not password:
            raise LdapError("config_error", "пустой пароль сервисной учётной записи")
    return _Cfg(id=str(d.id), kind=kind, hosts=hosts, port=int(d.port or (389 if enc != "ldaps" else 636)), encryption=enc,
                verify_tls=d.verify_tls is not False, ca_cert=ca, bind_dn=bind_dn, bind_password=password, base_dn=d.base_dn.strip(),
                user_filter=(d.user_filter or "").strip(), login_attr=d.login_attribute or "sAMAccountName", id_attr=d.id_attribute or "objectGUID",
                name_attr=d.name_attribute or "displayName", email_attr=d.email_attribute or "mail", phone_attr=d.phone_attribute or "mobile",
                nested=d.nested_groups is not False, allowed=tuple(str(g) for g in (d.allowed_groups or []) if str(g).strip()),
                timeout=max(1, min(60, int(d.timeout or 5))))


# ------------------------------------------------------------------------------------------------ соединения
def _tls(cfg: _Cfg) -> Tls | None:
    if cfg.encryption == "none":
        return None
    return Tls(validate=ssl.CERT_REQUIRED if cfg.verify_tls else ssl.CERT_NONE, version=ssl.PROTOCOL_TLS_CLIENT, ca_certs_data=cfg.ca_cert or None)


def _server(cfg: _Cfg, host: str) -> Server:
    return Server(host, port=cfg.port, use_ssl=cfg.encryption == "ldaps", tls=_tls(cfg), connect_timeout=cfg.timeout, get_info=NONE)


_pools: dict[str, tuple[tuple, ServerPool]] = {}
_pools_lock = threading.Lock()


def _pool(cfg: _Cfg) -> ServerPool:
    """Пул на каталог: состояние «контроллер недоступен» живёт POOL_EXHAUST_SECONDS и переживает отдельные входы."""
    key = (cfg.hosts, cfg.port, cfg.encryption, cfg.verify_tls, cfg.ca_cert, cfg.timeout)
    with _pools_lock:
        cached = _pools.get(cfg.id)
        if cached is None or cached[0] != key:
            # active=1 — один проход по контроллерам; active=True в ldap3 перебирает их бесконечно, и вход зависал бы при отказе всех
            cached = (key, ServerPool([_server(cfg, h) for h in cfg.hosts], FIRST, active=1, exhaust=POOL_EXHAUST_SECONDS))
            _pools[cfg.id] = cached
        pool = cached[1]
        state = pool._pool_state
        if state is not None and not any(s.available for s in state.server_states):
            for s in state.server_states:   # все помечены недоступными — пробуем снова, а не отказываем 60 с без попытки
                s.available = True
    return pool


def _exc_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


_TLS_WORDS = ("certificate", "verify failed", "hostname", "handshake", "wrong version", "unknown protocol", "tlsv1 alert", "sslv3 alert", "starttls")


def _transport_error(exc: BaseException) -> LdapError:
    text = _exc_text(exc)
    short = text if len(text) <= 300 else text[:297] + "…"
    if isinstance(exc, (LDAPCertificateError, LDAPStartTLSError, ssl.SSLCertVerificationError)) or any(w in text.lower() for w in _TLS_WORDS):
        return LdapError("tls_error", short)
    if "server pool" in text.lower():
        return LdapError("unavailable", "ни один контроллер не ответил")
    return LdapError("unavailable", short)


def _close(conn: Connection | None) -> None:
    if conn is None:
        return
    with contextlib.suppress(Exception):
        conn.unbind()
    if conn.server_pool is not None:
        conn.server_pool.pool_states.pop(conn, None)   # ldap3 сам не удаляет соединения из пула — без этого утечка памяти


def _connect(cfg: _Cfg, user: str, password: str, server: Server | ServerPool | None = None) -> Connection:
    """Открытое соединение после успешного simple bind. Пустой пароль — отказ без обращения к серверу."""
    if not password:
        raise LdapError("invalid_credentials", "пустой пароль")
    conn = Connection(server or _pool(cfg), user=user, password=password, authentication=SIMPLE, auto_bind=False, raise_exceptions=False,
                      read_only=True, receive_timeout=cfg.timeout, auto_referrals=False)
    try:
        conn.open(read_server_info=False)
        if cfg.encryption == "starttls" and not conn.start_tls(read_server_info=False):
            raise LdapError("tls_error", "сервер отклонил STARTTLS")
        ok = conn.bind(read_server_info=False)
    except LdapError:
        _close(conn)
        raise
    except (LDAPException, OSError) as exc:
        _close(conn)
        raise _transport_error(exc) from None
    finally:
        conn.password = None
    if not ok:
        result = conn.result
        _close(conn)
        raise LdapError(classify_bind(result), bind_detail(result))
    return conn


def _service(cfg: _Cfg, server=None) -> Connection:
    """Bind сервисной записью. Её отказ — ошибка настройки, а не «неверный пароль» пользователя."""
    try:
        return _connect(cfg, cfg.bind_dn, cfg.bind_password, server)
    except LdapError as exc:
        if exc.code in ("unavailable", "tls_error"):
            raise
        raise LdapError("config_error", f"сервисная учётная запись {cfg.bind_dn} не вошла: {exc.detail or exc.code}") from None


def _self_bind_name(cfg: _Cfg, login: str) -> str:
    """Имя для bind пользователем, когда сервисной записи нет. AD принимает UPN и DOMAIN\\user; OpenLDAP — только DN."""
    name, upn = parse_login(login)
    if cfg.kind == "ad":
        if "\\" in login or upn:
            return login.strip()
        domain = ".".join(v for a, v, _s in parse_dn(cfg.base_dn) if a.lower() == "dc")
        if not domain:
            raise LdapError("config_error", "без bind_dn вход по короткому имени требует base_dn вида DC=corp,DC=example")
        return f"{name}@{domain}"
    value = upn if (upn and cfg.login_attr.lower() == "mail") else name
    return f"{cfg.login_attr}={escape_rdn(value)},{cfg.base_dn}"


# ------------------------------------------------------------------------------------------------ поиск и группы
def _values(entry: dict, attr: str) -> list:
    v = (entry.get("attributes") or {}).get(attr)
    return v if isinstance(v, list) else ([] if v in (None, "", b"") else [v])


def _first(entry: dict, attr: str) -> str | None:
    for v in _values(entry, attr):
        s = (v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)).strip()
        if s:
            return s
    return None


def _attrs(cfg: _Cfg) -> list[str]:
    names = [cfg.login_attr, cfg.id_attr, cfg.name_attr, cfg.email_attr, cfg.phone_attr, "memberOf"]
    if cfg.kind == "ad":
        names += ["userAccountControl", "userPrincipalName"]
    return list(dict.fromkeys(names))


def user_filter(cfg: _Cfg, login: str) -> str:
    name, upn = parse_login(login)
    conds = [f"({cfg.login_attr}={escape_filter_chars(name)})"]
    if upn:
        conds.append(f"({cfg.login_attr}={escape_filter_chars(upn)})")
        if cfg.kind == "ad":
            conds.append(f"(userPrincipalName={escape_filter_chars(upn)})")
    cond = conds[0] if len(conds) == 1 else "(|" + "".join(conds) + ")"
    base = cfg.user_filter
    if base and not base.startswith("("):
        base = f"({base})"
    return f"(&{base}{cond})" if base else cond


def _search_failed(conn: Connection, what: str) -> LdapError:
    res = conn.result or {}
    code = res.get("result")
    if code in (51, 52, 81, 85):
        return LdapError("unavailable", f"{what}: LDAP {code} {res.get('description', '')}")
    if code == 32:
        return LdapError("config_error", f"{what}: base DN не найден")
    return LdapError("config_error", f"{what}: LDAP {code} {res.get('description', '')}".strip())


def _find(conn: Connection, cfg: _Cfg, login: str) -> dict | None:
    """Запись пользователя или None. Несколько записей — not_found (неоднозначность не угадываем), кроме точного совпадения UPN."""
    try:
        conn.search(cfg.base_dn, user_filter(cfg, login), search_scope=SUBTREE, attributes=_attrs(cfg), size_limit=3)
    except LDAPException as exc:
        raise _transport_error(exc) from None
    entries = [e for e in (conn.response or []) if e.get("type") == "searchResEntry"]
    if (conn.result or {}).get("result") not in (0, 4) and not entries:   # 4 — sizeLimitExceeded
        raise _search_failed(conn, "поиск пользователя")
    if len(entries) > 1:
        _name, upn = parse_login(login)
        exact = [e for e in entries if upn and any(str(v).lower() == upn.lower() for a in ("userPrincipalName", cfg.login_attr) for v in _values(e, a))]
        if len(exact) == 1:
            return exact[0]
        raise LdapError("not_found", f"под логин подходит несколько записей ({len(entries)}); уточните user_filter")
    return entries[0] if entries else None


def _find_self(conn: Connection, cfg: _Cfg, login: str) -> dict | None:
    """Своя запись после bind пользователем. OpenLDAP: DN известен — читаем его (правила «by self read» не открывают base_dn)."""
    if cfg.kind == "ad":
        return _find(conn, cfg, login)
    try:
        conn.search(_self_bind_name(cfg, login), "(objectClass=*)", search_scope=BASE, attributes=_attrs(cfg))
    except LDAPException as exc:
        raise _transport_error(exc) from None
    return next((e for e in (conn.response or []) if e.get("type") == "searchResEntry"), None)


def _check_enabled(cfg: _Cfg, entry: dict) -> None:
    uac = _first(entry, "userAccountControl") if cfg.kind == "ad" else None
    if uac and uac.isdigit() and int(uac) & _UAC_DISABLED:
        raise LdapError("account_disabled", "userAccountControl: ACCOUNTDISABLE")


def _ad_groups(conn: Connection, cfg: _Cfg, dn: str, member_of: list[str]) -> list[str]:
    if cfg.nested:
        flt = f"(&(objectClass=group)(member:{LDAP_MATCHING_RULE_IN_CHAIN}:={escape_filter_chars(dn)}))"
        try:
            found = [str(e["dn"]) for e in conn.extend.standard.paged_search(cfg.base_dn, flt, search_scope=SUBTREE, attributes=_NO_ATTRS,
                                                                             paged_size=500, generator=True) if e.get("type") == "searchResEntry"]
            if (conn.result or {}).get("result") == 0:
                return found + member_of
            code = (conn.result or {}).get("result")
        except Exception as exc:  # noqa: BLE001
            code = type(exc).__name__
        # неполный список групп мог бы обойти запрещающие политики и исключения — отказ, как в _walk_groups
        log.warning("LDAP_MATCHING_RULE_IN_CHAIN не выполнен (%s): вход отклонён", code)
        raise LdapError("unavailable", f"не удалось вычислить вложенные группы ({code})")
    return member_of


def _walk_groups(conn: Connection, cfg: _Cfg, dn: str, member_of: list[str]) -> list[str]:
    """OpenLDAP: группы, где участник — пользователь, затем группы этих групп; глубина ≤ MAX_GROUP_DEPTH, каждая группа один раз."""
    found: dict[str, str] = {g.lower(): g for g in member_of}
    frontier = [dn, *member_of] if cfg.nested else [dn]
    for _level in range(MAX_GROUP_DEPTH if cfg.nested else 1):
        nxt: list[str] = []
        for i in range(0, len(frontier), 50):   # фильтр из десятков условий, а не из тысяч
            ors = "".join(f"(member={escape_filter_chars(x)})(uniqueMember={escape_filter_chars(x)})" for x in frontier[i:i + 50])
            try:
                conn.search(cfg.base_dn, f"(&{_GROUP_CLASSES}(|{ors}))", search_scope=SUBTREE, attributes=_NO_ATTRS)
            except LDAPException as exc:
                raise _transport_error(exc) from None
            if (conn.result or {}).get("result") not in (0, 4):
                raise _search_failed(conn, "поиск групп")   # неполный список групп мог бы обойти запрещающие политики
            for e in conn.response or []:
                if e.get("type") == "searchResEntry" and str(e["dn"]).lower() not in found:
                    found[str(e["dn"]).lower()] = str(e["dn"])
                    nxt.append(str(e["dn"]))
        if not nxt:
            break
        frontier = nxt
    return list(found.values())


def _groups(conn: Connection, cfg: _Cfg, entry: dict) -> list[str]:
    member_of = [str(v) for v in _values(entry, "memberOf")]
    dns = _ad_groups(conn, cfg, str(entry["dn"]), member_of) if cfg.kind == "ad" else _walk_groups(conn, cfg, str(entry["dn"]), member_of)
    return _with_cn(dns)


def external_id(cfg: _Cfg, entry: dict) -> str:
    """objectGUID (байты) → UUID-строка; прочие атрибуты (entryUUID, uid…) — как есть. Без устойчивого идентификатора вход не выполняется."""
    if cfg.id_attr.lower() == "objectguid":
        raw = ((entry.get("raw_attributes") or {}).get(cfg.id_attr) or [b""])[0]
        if isinstance(raw, (bytes, bytearray)) and len(raw) == 16:
            return guid_to_str(raw)
    else:
        value = _first(entry, cfg.id_attr)
        if value:
            return value
    raise LdapError("config_error", f"у записи нет атрибута-идентификатора {cfg.id_attr}")


def _user(cfg: _Cfg, entry: dict, groups: list[str], login: str) -> DirectoryUser:
    name = _first(entry, cfg.login_attr) or parse_login(login)[0]
    return DirectoryUser(dn=str(entry["dn"]), external_id=external_id(cfg, entry), login=name, display_name=_first(entry, cfg.name_attr) or name,
                         email=_first(entry, cfg.email_attr), phone=_first(entry, cfg.phone_attr), groups=groups)


def _finish(conn: Connection, cfg: _Cfg, entry: dict, login: str) -> DirectoryUser:
    user = _user(cfg, entry, _groups(conn, cfg, entry), login)
    if not group_allowed(user.groups, list(cfg.allowed)):
        raise LdapError("not_allowed_group", f"{user.login} не входит ни в одну из разрешённых групп")
    return user


# ------------------------------------------------------------------------------------------------ синхронные операции
def _authenticate(cfg: _Cfg, login: str, password: str) -> DirectoryUser:
    if not cfg.bind_dn:   # без сервисной записи: bind самим пользователем, затем чтение своей записи его же правами
        conn = _connect(cfg, _self_bind_name(cfg, login), password)
        try:
            entry = _find_self(conn, cfg, login)
            if entry is None:
                raise LdapError("not_found", "пароль принят, но своя запись пользователю не видна (проверьте base_dn и права чтения)")
            return _finish(conn, cfg, entry, login)
        finally:
            _close(conn)
    svc = _service(cfg)
    try:
        entry = _find(svc, cfg, login)
        if entry is None:
            raise LdapError("not_found")
        _check_enabled(cfg, entry)
        _close(_connect(cfg, str(entry["dn"]), password))
        return _finish(svc, cfg, entry, login)
    finally:
        _close(svc)


def _lookup(cfg: _Cfg, login: str) -> DirectoryUser | None:
    if not cfg.bind_dn:
        raise LdapError("config_error", "поиск без пароля пользователя требует сервисной учётной записи (bind_dn)")
    svc = _service(cfg)
    try:
        entry = _find(svc, cfg, login)
        return None if entry is None else _user(cfg, entry, _groups(svc, cfg, entry), login)
    finally:
        _close(svc)


def _diagnose(cfg: _Cfg, login: str | None, password: str | None) -> list[dict]:
    steps: list[dict] = []

    def step(name: str, ok: bool, detail: str, t0: float, host: str = "") -> bool:
        steps.append({"step": name, "ok": ok, "detail": detail, "ms": int((time.perf_counter() - t0) * 1000), "host": host})
        return ok

    healthy: list[str] = []
    for host in cfg.hosts:
        t = time.perf_counter()
        try:
            addr = socket.getaddrinfo(host, cfg.port, type=socket.SOCK_STREAM)[0][4][0]
            step("dns", True, f"{host} → {addr}", t, host)
        except OSError as exc:
            step("dns", False, f"имя {host} не разрешается ({exc.strerror or type(exc).__name__})", t, host)
            continue
        t = time.perf_counter()
        try:
            socket.create_connection((host, cfg.port), timeout=cfg.timeout).close()
            step("tcp", True, f"порт {cfg.port} открыт", t, host)
        except OSError as exc:
            step("tcp", False, f"нет соединения с {host}:{cfg.port} ({exc.strerror or type(exc).__name__}): порт, межсетевой экран, маршрут", t, host)
            continue
        t = time.perf_counter()
        if cfg.encryption == "none":
            step("tls", True, "шифрование выключено (encryption=none): пароли передаются открытым текстом", t, host)
        else:
            conn = Connection(_server(cfg, host), auto_bind=False, raise_exceptions=False, read_only=True, receive_timeout=cfg.timeout, auto_referrals=False)
            try:
                conn.open(read_server_info=False)
                if cfg.encryption == "starttls" and not conn.start_tls(read_server_info=False):
                    raise LdapError("tls_error", "сервер отклонил STARTTLS")
                version = conn.socket.version() if hasattr(conn.socket, "version") else "TLS"
                trust = "без проверки сертификата (verify_tls выключен)" if not cfg.verify_tls else \
                    ("сертификат проверен по ca_cert" if cfg.ca_cert else "сертификат проверен по системному набору CA")
                step("tls", True, f"{cfg.encryption}, {version}, {trust}", t, host)
            except LdapError as exc:
                step("tls", False, exc.detail, t, host)
                continue
            except (LDAPException, OSError) as exc:
                step("tls", False, _transport_error(exc).detail, t, host)
                continue
            finally:
                _close(conn)
        healthy.append(host)
    if not healthy:
        return steps

    pool = ServerPool([_server(cfg, h) for h in healthy], FIRST, active=1)   # свежий пул: диагностика не наследует «исключённые» узлы
    t = time.perf_counter()
    if cfg.bind_dn:
        try:
            conn = _service(cfg, pool)
        except LdapError as exc:
            step("bind", False, exc.detail, t)
            return steps
        step("bind", True, f"сервисная учётная запись {cfg.bind_dn} вошла", t, conn.server.host)
    elif login and password:
        try:
            conn = _connect(cfg, _self_bind_name(cfg, login), password, pool)
        except LdapError as exc:
            step("bind", False, f"bind_dn не задан, bind пользователем: {exc.code} ({exc.detail})", t)
            return steps
        step("bind", True, "bind_dn не задан: пользователь вошёл своим паролем, поиск выполняется его правами", t, conn.server.host)
    else:
        step("bind", False, "bind_dn не задан: для проверки укажите логин и пароль пользователя", t)
        return steps
    host = conn.server.host
    try:
        t = time.perf_counter()
        try:
            ok = conn.search(cfg.base_dn, "(objectClass=*)", search_scope=BASE, attributes=_NO_ATTRS)
        except LDAPException as exc:
            ok, conn.result = False, {"description": _transport_error(exc).detail}
        if not step("base_dn", bool(ok), f"{cfg.base_dn} доступен для чтения" if ok else
                    f"{cfg.base_dn} не читается ({(conn.result or {}).get('description', 'ошибка')}): проверьте значение и права", t, host):
            return steps
        if not login:
            return steps
        t = time.perf_counter()
        try:
            entry = _find(conn, cfg, login) if cfg.bind_dn else _find_self(conn, cfg, login)
        except LdapError as exc:
            step("user_search", False, f"{exc.code}: {exc.detail}", t, host)
            return steps
        if not step("user_search", entry is not None, f"найден {entry['dn']}" if entry else f"«{login}» не найден по фильтру {user_filter(cfg, login)}", t, host):
            return steps
        if password and cfg.bind_dn:
            t = time.perf_counter()
            try:
                _check_enabled(cfg, entry)
                _close(_connect(cfg, str(entry["dn"]), password, pool))
                step("user_bind", True, "пароль пользователя принят", t, host)
            except LdapError as exc:
                step("user_bind", False, f"{exc.code}: {exc.detail}" if exc.detail else exc.code, t, host)
                return steps
        elif password:
            step("user_bind", True, "пароль пользователя принят (bind пользователем выше)", t, host)
        t = time.perf_counter()
        try:
            groups = _groups(conn, cfg, entry)
        except LdapError as exc:
            step("groups", False, f"{exc.code}: {exc.detail}", t, host)
            return steps
        dns = [g for g in groups if "=" in g]
        shown = ", ".join(_cn(g) or g for g in dns[:20]) + (" …" if len(dns) > 20 else "")
        allowed = group_allowed(groups, list(cfg.allowed))
        step("groups", allowed, f"групп с учётом вложенности: {len(dns)} ({shown or 'нет'})" +
             ("" if allowed else "; ни одна не входит в allowed_groups — вход будет отклонён"), t, host)
        return steps
    finally:
        _close(conn)


# ------------------------------------------------------------------------------------------------ асинхронный интерфейс
async def authenticate(directory, login: str, password: str) -> DirectoryUser:
    """Проверка пароля: поиск сервисной записью (или bind пользователем, если bind_dn пуст), bind DN пользователя его паролем,
    группы с вложенностью, allowed_groups. Ошибки — LdapError с кодом из CODES."""
    if not password:
        raise LdapError("invalid_credentials", "пустой пароль")
    parse_login(login)
    cfg = _snapshot(directory)
    return await asyncio.to_thread(_authenticate, cfg, login, password)


async def lookup(directory, login: str) -> DirectoryUser | None:
    """Пользователь и его группы без пароля (нужна сервисная запись). allowed_groups не применяется: решение принимает вызывающий."""
    try:
        parse_login(login)
    except LdapError:
        return None
    cfg = _snapshot(directory)
    return await asyncio.to_thread(_lookup, cfg, login)


async def diagnose(directory, login: str | None = None, password: str | None = None) -> list[dict]:
    """Шаги dns → tcp → tls (по каждому хосту) → bind → base_dn → user_search → user_bind → groups; до первой критичной ошибки.
    Неверная настройка каталога — LdapError(config_error) до сетевых шагов."""
    cfg = _snapshot(directory)
    return await asyncio.to_thread(_diagnose, cfg, (login or "").strip() or None, password or None)
