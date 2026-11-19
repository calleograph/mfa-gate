"""Сценарий аутентификации, общий для RADIUS и REST API.

Порядок: блокировка → первый фактор → политика → второй фактор. Любая невозможность проверить фактор — отказ, если политика явно
не разрешает иное (on_unavailable=allow; такой обход журналируется как warning).
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import AuthRequest, Device, Group, LdapDirectory, OneTimeToken, Policy, PushOutbox, Resource, TotpFactor, User
from ..security import totp
from ..security.crypto import b64url, box, decision_message, verify_decision, verify_password
from . import events
from .audit import audit
from .policy import effective_groups, evaluate

log = logging.getLogger(__name__)

# причины отказа — стабильные коды для журналов, API и экрана «Проблемы входа»
REASONS = {
    "local_password": "Неверный логин или пароль (локальная учётная запись).",
    "ldap_password": "Каталог не принял пароль.",
    "unknown_user": "Пользователь не найден ни локально, ни в каталогах.",
    "user_disabled": "Учётная запись отключена.",
    "locked": "Временная блокировка после ошибок входа.",
    "directory_unavailable": "Каталог LDAP/AD недоступен — вход запрещён.",
    "directory_denied": "Каталог отклонил вход (учётная запись заблокирована, пароль истёк или нет нужной группы).",
    "policy_deny": "Доступ запрещён политикой.",
    "no_factor": "У пользователя нет доступного второго фактора.",
    "invalid_otp": "Неверный или уже использованный одноразовый код.",
    "push_denied": "Пользователь отклонил запрос.",
    "push_expired": "Нет ответа на push в отведённое время.",
    "resource_disabled": "Ресурс отключён.",
}


# причины до второго фактора наружу (Reply-Message, экран входа) не различаются: иначе по ответу можно перечислять логины
PRIMARY_REASONS = {"local_password", "ldap_password", "unknown_user", "user_disabled", "locked", "directory_denied"}
GENERIC_PRIMARY = "Неверный логин или пароль, либо учётная запись временно заблокирована."


def public_message(reason: str | None) -> str:
    return GENERIC_PRIMARY if reason in PRIMARY_REASONS else REASONS.get(reason or "", "")


@dataclass
class Outcome:
    status: str                       # accept | reject | pending | challenge
    reason: str | None = None
    request_id: uuid.UUID | None = None
    message: str = ""


def _now() -> datetime:
    return datetime.now(UTC)


async def _record_failure(session: AsyncSession, user: User) -> None:
    """Счётчик ошибок одним UPDATE; после истечения блокировки счёт начинается заново."""
    cfg = settings()
    await session.execute(text("""
        UPDATE users SET
          failed_attempts = CASE WHEN locked_until IS NOT NULL AND locked_until <= now() THEN 1 ELSE failed_attempts + 1 END,
          locked_until = CASE
            WHEN (CASE WHEN locked_until IS NOT NULL AND locked_until <= now() THEN 1 ELSE failed_attempts + 1 END) >= :threshold
            THEN now() + make_interval(mins => :minutes) ELSE
              CASE WHEN locked_until IS NOT NULL AND locked_until <= now() THEN NULL ELSE locked_until END END
        WHERE id = :id"""), {"id": user.id, "threshold": cfg.lockout_threshold, "minutes": cfg.lockout_minutes})


async def _provision(session: AsyncSession, directory: LdapDirectory, found) -> User | None:
    """Пользователь каталога: связь по external_id (переименование в AD не создаёт дубль), затем по логину."""
    user = None
    if found.external_id:
        user = await session.scalar(select(User).where(User.directory_id == directory.id, User.external_id == found.external_id))
    if user is None:
        user = await session.scalar(select(User).where(User.login == found.login.lower()))
        # запись с тем же логином принимается, только если это тот же человек из того же каталога: иначе доменная учётная запись
        # «унаследовала» бы роль, телефон и TOTP локального пользователя или уволенного сотрудника с тем же логином
        if user is not None and not (user.source == "ldap" and user.directory_id in (None, directory.id)
                                     and user.external_id in (None, found.external_id)):
            audit(session, "auth.identity_conflict", actor=found.login.lower(), target=directory.name, result="fail", severity="critical",
                  existing_source=user.source)
            return None
    if user is None:
        user = User(login=found.login.lower(), source="ldap", groups=[])
        session.add(user)
    user.source, user.directory_id, user.external_id = "ldap", directory.id, found.external_id
    user.display_name = found.display_name or user.display_name
    user.email, user.phone = found.email or user.email, found.phone or user.phone
    user.directory_groups = list(found.groups)
    await session.flush()
    return user


async def find_user(session: AsyncSession, login: str) -> User | None:
    """DOMAIN\\user и UPN — та же доменная запись: блокировка и счётчик ошибок общие."""
    from . import ldap as ldap_service   # noqa: PLC0415

    login = login.strip().lower()
    user = await session.scalar(select(User).where(User.login == login))
    if user is None:
        short = ldap_service.parse_login(login)[0].lower()
        if short != login:
            user = await session.scalar(select(User).where(User.login == short, User.source == "ldap"))
    return user


async def primary(session: AsyncSession, login: str, password: str) -> tuple[User | None, str | None]:
    """Первый фактор. Возвращает (пользователь, None) или (пользователь|None, код причины)."""
    from . import ldap as ldap_service   # noqa: PLC0415 — модуль тянет ldap3, нужен только здесь

    login = login.strip().lower()
    user = await find_user(session, login)
    if user is not None and user.locked_until and user.locked_until > _now():
        return user, "locked"
    if user is not None and user.source == "local" and user.login == login:
        if not await asyncio.to_thread(verify_password, password, user.password_hash):
            await _record_failure(session, user)
            return user, "local_password"
        if user.status != "active":
            return user, "user_disabled"
        return user, None

    directories = (await session.scalars(select(LdapDirectory).where(LdapDirectory.enabled).order_by(LdapDirectory.priority))).all()
    if user is not None and user.directory_id:
        directories.sort(key=lambda d: d.id != user.directory_id)   # сначала каталог, из которого пользователь пришёл
    if not directories:
        await asyncio.to_thread(verify_password, password, None)   # время ответа как у существующей учётной записи
        return user, "unknown_user"
    unavailable = False
    for directory in directories:
        try:
            found = await ldap_service.authenticate(directory, login, password)
        except ldap_service.LdapError as exc:
            if exc.code == "not_found":
                continue
            if exc.code in ("unavailable", "tls_error", "config_error"):
                unavailable = True
                log.warning("каталог %s недоступен: %s", directory.name, exc.code)
                continue
            if exc.code != "invalid_credentials":   # отключена, истекла, нет группы — не подбор пароля: в счётчик не идёт
                return user, "directory_denied"
            if user is not None:
                await _record_failure(session, user)
            return user, "ldap_password"
        provisioned = await _provision(session, directory, found)
        if provisioned is None:
            return user, "directory_denied"
        user = provisioned
        if user.locked_until and user.locked_until > _now():
            return user, "locked"
        if user.status != "active":
            return user, "user_disabled"
        return user, None
    return user, "directory_unavailable" if unavailable else "unknown_user"


async def trusted_identity(session: AsyncSession, login: str) -> tuple[User | None, str | None]:
    """Пароль уже проверил шлюз или приложение: запись и группы доменного пользователя берутся свежими из каталога сервисной
    учётной записью — новый сотрудник заводится сам, а исключённый из группы или отключённый в каталоге не проходит по старым данным.
    Каталог недоступен — отказ, как при входе с паролем; каталог без сервисной записи (поиск без пароля невозможен) — по сохранённой записи."""
    from . import ldap as ldap_service   # noqa: PLC0415

    user = await find_user(session, login)
    if user is not None and user.source == "local":
        return user, None
    directories = (await session.scalars(select(LdapDirectory).where(LdapDirectory.enabled).order_by(LdapDirectory.priority))).all()
    if user is not None and user.directory_id:
        directories.sort(key=lambda d: d.id != user.directory_id)
    reachable, gone, down = False, False, False   # gone — свой каталог ответил, что записи нет; down — каталог не ответил
    for directory in directories:
        try:
            found = await ldap_service.lookup(directory, login, check=True)
        except ldap_service.LdapError as exc:
            if exc.code in ("unavailable", "tls_error", "config_error"):
                log.warning("каталог %s: поиск без пароля невозможен: %s", directory.name, exc.code)
                down = down or exc.code != "config_error"
                continue
            if exc.code != "not_found":
                return user, "directory_denied"
            found = None
        reachable = True
        if found is None:
            gone = gone or (user is not None and user.directory_id == directory.id)
            continue
        provisioned = await _provision(session, directory, found)
        return (user, "directory_denied") if provisioned is None else (provisioned, None)
    if gone:
        return user, "directory_denied"
    if down:   # не смогли проверить, не отключён ли сотрудник: отказ, а не вход по устаревшей записи
        return user, "directory_unavailable"
    if user is not None:
        return user, None
    return None, "directory_unavailable" if directories and not reachable else "unknown_user"


async def _groups(session: AsyncSession, user: User) -> set[str]:
    mapped = (await session.scalars(select(Group).where(Group.directory_group.is_not(None)))).all()
    return effective_groups(user.groups, user.directory_groups or [], mapped)


async def verify_totp(session: AsyncSession, user: User, code: str) -> bool:
    """Проверка по всем активным TOTP пользователя; принятый шаг фиксируется условным UPDATE (повтор кода не пройдёт)."""
    factors = (await session.scalars(select(TotpFactor).where(TotpFactor.user_id == user.id, TotpFactor.status == "active"))).all()
    for factor in factors:
        secret = box().decrypt(factor.secret_enc, aad=b"totp:" + str(factor.id).encode())
        step = totp.verify(secret, code, factor.last_step)
        if step is None:
            continue
        res = await session.execute(update(TotpFactor)
                                    .where(TotpFactor.id == factor.id, (TotpFactor.last_step.is_(None)) | (TotpFactor.last_step < step))
                                    .values(last_step=step, last_used_at=_now()))
        if res.rowcount == 1:
            return True
    return False


def _finish(session: AsyncSession, req: AuthRequest, status: str, reason: str | None = None, method: str | None = None) -> None:
    req.status, req.reason, req.decided_at = status, reason, _now()
    if method:
        req.method = method
    ok = status in ("approved", "allowed")
    audit(session, "auth.accept" if ok else "auth.reject", actor=req.login, target=req.resource_name, ip=req.source_ip,
          result="ok" if ok else "fail", severity="warning" if reason == "mfa_bypass_unavailable" else "info",
          request_id=str(req.id), channel=req.channel, reason=reason, method=req.method)


async def start_push(session: AsyncSession, user: User, req: AuthRequest, devices) -> None:
    """Открывает push-транзакцию: challenge, срок, очередь доставки на все активные устройства, события для long-poll и worker."""
    req.user_id, req.status = user.id, "pending"
    req.method, req.challenge = "push", b64url(secrets.token_bytes(32))
    req.expires_at = _now() + timedelta(seconds=settings().push_timeout)
    session.add(req)
    await session.flush()
    for device in devices:
        session.add(PushOutbox(auth_request_id=req.id, device_id=device.id))
    await events.notify(session, f"user:{user.id}")
    await events.notify(session, "push")
    audit(session, "auth.push_sent", actor=req.login, target=req.resource_name, ip=req.source_ip, request_id=str(req.id), devices=len(devices))


async def begin(session: AsyncSession, *, login: str, password: str | None, resource: Resource | None, channel: str,
                client_name: str = "", source_ip: str | None = None, nas_ip: str | None = None, otp: str | None = None,
                api_client_id: uuid.UUID | None = None) -> Outcome:
    """Новая попытка входа. password=None — первый фактор уже проверен шлюзом (режим «только второй фактор» REST API)."""
    # id задаётся сразу: ответ (в том числе отказ) ссылается на транзакцию до записи в БД
    req = AuthRequest(id=uuid.uuid4(), login=login.strip().lower(), channel=channel, client_name=client_name, source_ip=source_ip, nas_ip=nas_ip,
                      resource_id=resource.id if resource else None, resource_name=resource.name if resource else "", status="pending",
                      api_client_id=api_client_id)

    def reject(reason: str) -> Outcome:
        session.add(req)
        _finish(session, req, "rejected", reason)
        return Outcome("reject", reason, req.id, public_message(reason))

    if resource is not None and not resource.enabled:
        return reject("resource_disabled")
    if password is None:
        user, reason = await trusted_identity(session, req.login)
        if not reason and user is not None:
            reason = "locked" if user.locked_until and user.locked_until > _now() else ("user_disabled" if user.status != "active" else None)
    else:
        user, reason = await primary(session, login, password)
    if user is not None:
        req.user_id = user.id
    if reason:
        return reject(reason)

    policies = (await session.scalars(select(Policy).where(Policy.enabled))).all()
    decision = evaluate(policies, resource.id if resource else None, await _groups(session, user))
    req.policy_id = decision.policy.id if decision.policy else None
    if decision.action == "deny":
        return reject("policy_deny")
    if decision.action == "allow":
        session.add(req)
        _finish(session, req, "allowed", "policy_allow", "policy")
        await _login_ok(session, user)
        return Outcome("accept", None, req.id)

    if otp:
        if "totp" in decision.methods and await verify_totp(session, user, otp):
            session.add(req)
            _finish(session, req, "approved", None, "totp")
            await _login_ok(session, user)
            return Outcome("accept", None, req.id)
        await _record_failure(session, user)
        return reject("invalid_otp")

    devices = (await session.scalars(select(Device).where(Device.user_id == user.id, Device.status == "active"))).all()
    if "push" in decision.methods and devices:
        # повтор той же попытки (шлюз заново отправил Access-Request) присоединяется к открытой транзакции без нового push.
        # Только при известном адресе пользователя и полном совпадении канала, клиента и NAS: иначе чужая попытка с тем же
        # паролем могла бы «пристроиться» к push жертвы. Остаточный риск — общий NAT (см. docs/threat-model.md).
        existing = None if not source_ip else await session.scalar(select(AuthRequest).where(
            AuthRequest.user_id == user.id, AuthRequest.status == "pending", AuthRequest.method == "push",
            AuthRequest.resource_id.is_not_distinct_from(req.resource_id), AuthRequest.source_ip == source_ip,
            AuthRequest.channel == channel, AuthRequest.client_name == client_name,
            AuthRequest.api_client_id.is_not_distinct_from(api_client_id), AuthRequest.nas_ip.is_not_distinct_from(nas_ip),
            AuthRequest.expires_at > _now()).order_by(AuthRequest.created_at.desc()).limit(1))
        if existing is not None:
            return Outcome("pending", None, existing.id, "Ожидание подтверждения на телефоне")
        await start_push(session, user, req, devices)
        return Outcome("pending", None, req.id, "Подтвердите вход в приложении MFA Gate")

    has_totp = await session.scalar(select(TotpFactor.id).where(TotpFactor.user_id == user.id, TotpFactor.status == "active").limit(1))
    if "totp" in decision.methods and has_totp:
        req.method, req.challenge = "totp", b64url(secrets.token_bytes(16))
        req.expires_at = _now() + timedelta(seconds=settings().otp_challenge_timeout)
        session.add(req)
        await session.flush()
        return Outcome("challenge", None, req.id, "Введите одноразовый код")

    if decision.on_unavailable == "allow":
        session.add(req)
        _finish(session, req, "allowed", "mfa_bypass_unavailable", "policy")
        await _login_ok(session, user)
        return Outcome("accept", None, req.id)
    return reject("no_factor")


async def factor_activated(session: AsyncSession, user: User) -> None:
    """Подтверждён новый второй фактор: дальше самостоятельная регистрация по одному паролю закрыта, разрешение на перерегистрацию погашено."""
    user.mfa_enrolled_at = user.mfa_enrolled_at or _now()
    await session.execute(update(OneTimeToken).where(OneTimeToken.user_id == user.id, OneTimeToken.purpose == "reenroll",
                                                     OneTimeToken.used_at.is_(None)).values(used_at=_now()))
    # ссылки привязки, выпущенные самим сотрудником (в том числе в сессии «только пароль»), не переживают подтверждение фактора
    await session.execute(update(OneTimeToken).where(OneTimeToken.user_id == user.id, OneTimeToken.purpose == "enroll_device",
                                                     OneTimeToken.created_by == user.login, OneTimeToken.used_at.is_(None))
                          .values(used_at=_now()))


async def _login_ok(session: AsyncSession, user: User) -> None:
    user.failed_attempts, user.locked_until, user.last_login_at = 0, None, _now()


async def answer_challenge(session: AsyncSession, request_id: uuid.UUID, otp: str) -> Outcome:
    """Ответ на Access-Challenge: код TOTP по ранее созданной транзакции."""
    req = await session.get(AuthRequest, request_id, with_for_update=True)
    if req is None or req.status != "pending" or req.method != "totp":
        return Outcome("reject", "invalid_otp", request_id, REASONS["invalid_otp"])
    if req.expires_at and req.expires_at <= _now():
        _finish(session, req, "expired", "push_expired")
        return Outcome("reject", "push_expired", req.id, "Время на ввод кода истекло")
    user = await session.get(User, req.user_id)
    if user is not None and await verify_totp(session, user, otp):
        _finish(session, req, "approved", None, "totp")
        await _login_ok(session, user)
        return Outcome("accept", None, req.id)
    if user is not None:
        await _record_failure(session, user)
    _finish(session, req, "rejected", "invalid_otp")
    return Outcome("reject", "invalid_otp", req.id, REASONS["invalid_otp"])


async def decide(session: AsyncSession, request_id: uuid.UUID, device: Device, decision: str, signature: str) -> str:
    """Решение устройства. Возвращает новый статус или код ошибки: not_found | bad_signature | already_decided | expired."""
    req = await session.get(AuthRequest, request_id)
    if req is None or req.user_id != device.user_id or req.method != "push" or not req.challenge:
        return "not_found"
    message = decision_message(str(req.id), str(device.id), req.challenge, decision)
    if decision not in ("approve", "deny") or not verify_decision(device.public_key, message, signature):
        audit(session, "device.bad_signature", actor=req.login, target=str(device.id), result="fail", severity="warning", request_id=str(req.id))
        return "bad_signature"
    if req.status != "pending":
        return "already_decided"
    if req.expires_at and req.expires_at <= _now():
        return "expired"
    status, reason = ("approved", None) if decision == "approve" else ("denied", "push_denied")
    res = await session.execute(update(AuthRequest)
                                .where(AuthRequest.id == req.id, AuthRequest.status == "pending", AuthRequest.expires_at > _now())
                                .values(status=status, reason=reason, decided_at=_now(), device_id=device.id)
                                .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        return "already_decided"
    await session.refresh(req)
    audit(session, "auth.accept" if status == "approved" else "auth.reject", actor=req.login, target=req.resource_name, ip=req.source_ip,
          result="ok" if status == "approved" else "fail", severity="info" if status == "approved" else "warning",
          request_id=str(req.id), channel=req.channel, method="push", device=str(device.id), reason=reason)
    if status == "approved" and req.user_id:
        user = await session.get(User, req.user_id)
        if user is not None:
            await _login_ok(session, user)
    await events.notify(session, f"req:{req.id}")
    return status


async def expire(session: AsyncSession, request_id: uuid.UUID) -> None:
    """Запрос без ответа: фиксируем истечение, чтобы позднее подтверждение уже ничего не открыло."""
    res = await session.execute(update(AuthRequest).where(AuthRequest.id == request_id, AuthRequest.status == "pending")
                                .values(status="expired", reason="push_expired", decided_at=_now())
                                .returning(AuthRequest.login, AuthRequest.resource_name, AuthRequest.source_ip, AuthRequest.channel))
    row = res.first()
    if row:
        audit(session, "auth.reject", actor=row.login, target=row.resource_name, ip=row.source_ip, result="fail",
              request_id=str(request_id), channel=row.channel, reason="push_expired")


async def wait_decision(sessionmaker, request_id: uuid.UUID, deadline: float) -> AuthRequest | None:
    """Ждём решения до deadline (time.monotonic()). Событие будит сразу, опрос раз в 2 с страхует потерю NOTIFY."""
    while True:
        async with sessionmaker() as session:
            req = await session.get(AuthRequest, request_id)
            if req is None or req.status != "pending":
                return req
        left = deadline - time.monotonic()
        if left <= 0:
            return req
        await events.hub.wait(f"req:{request_id}", min(left, 2.0))
