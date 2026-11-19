"""Портал сотрудника: вход доменной учётной записью, первичная настройка второго фактора, управление своими факторами.

Вход: пароль → второй фактор (TOTP, push, резервный код). Без единого фактора — мастер настройки, но только при первой
регистрации (или по разрешению администратора), из сетей MFA_GATE_PORTAL_ENROLL_NETWORKS и для групп
MFA_GATE_PORTAL_ENROLL_GROUPS (docs/adr/0017). Добавлять и удалять факторы можно только в сессии, подтверждённой вторым фактором.
"""
from __future__ import annotations

import ipaddress
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..models import AdminSession, AuthRequest, Device, OneTimeToken, Policy, TotpFactor, User
from ..security import totp
from ..security.crypto import box, new_token, sha256_hex
from ..services import authflow
from ..services.audit import audit
from ..services.policy import DEFAULT_METHODS, evaluate
from .auth import CodeIn, LoginIn, check_lock, session_push, session_push_result
from .deps import PORTAL_COOKIE, client_ip, cookie_secure, login_limiter, portal_session
from .manage import issue_enrollment

router = APIRouter(prefix="/api/v1/portal", tags=["portal"])
TITLE = "Личный кабинет MFA Gate"
PENDING_MINUTES = 15          # сессия до второго фактора (вход или первичная настройка)
BACKUP_CODES = 10
BACKUP_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # без похожих символов: 0/o, 1/l/i


def _norm_code(code: str) -> str:
    return "".join(code.lower().split()).replace("-", "")


def _backup_hash(user: User, code: str) -> str:
    return totp.hash_backup(settings().master_key, str(user.id), _norm_code(code))


def new_backup_codes() -> list[str]:
    raw = ("".join(secrets.choice(BACKUP_ALPHABET) for _ in range(10)) for _ in range(BACKUP_CODES))
    return [f"{c[:5]}-{c[5:]}" for c in raw]


def _in_networks(ip: str, networks: str) -> bool:
    nets = [n.strip() for n in networks.split(",") if n.strip()]
    if not nets:
        return True
    try:
        addr = ipaddress.ip_address(ip)
        return any(addr in ipaddress.ip_network(n, strict=False) for n in nets)
    except ValueError:
        return False


async def _groups(session: AsyncSession, user: User) -> set[str]:
    return await authflow._groups(session, user)


async def _methods(session: AsyncSession, user: User) -> list[str]:
    """Способы, которые политика разрешает пользователю вне привязки к ресурсу."""
    policies = (await session.scalars(select(Policy).where(Policy.enabled))).all()
    decision = evaluate(policies, None, await _groups(session, user))
    return decision.methods if decision.action == "mfa" else list(DEFAULT_METHODS)


async def _factors(session: AsyncSession, user: User) -> dict:
    totps = (await session.scalars(select(TotpFactor).where(TotpFactor.user_id == user.id, TotpFactor.status == "active")
                                   .order_by(TotpFactor.created_at))).all()
    devices = (await session.scalars(select(Device).where(Device.user_id == user.id, Device.status == "active")
                                     .order_by(Device.created_at))).all()
    backup = await session.scalar(select(func.count()).select_from(OneTimeToken).where(
        OneTimeToken.user_id == user.id, OneTimeToken.purpose == "backup_code", OneTimeToken.used_at.is_(None)))
    dt = lambda v: v.isoformat() if v else None   # noqa: E731
    return {"totp": [{"id": str(f.id), "name": f.name, "created_at": dt(f.created_at), "last_used_at": dt(f.last_used_at)} for f in totps],
            "devices": [{"id": str(d.id), "name": d.name, "platform": d.platform, "app_version": d.app_version, "push": d.push_provider,
                         "created_at": dt(d.created_at), "last_seen_at": dt(d.last_seen_at)} for d in devices],
            "backup_codes_left": backup or 0}


async def _enroll_denied(session: AsyncSession, user: User, ip: str) -> str | None:
    """Почему нельзя настроить первый фактор по одному паролю (None — можно)."""
    cfg = settings()
    if user.mfa_enrolled_at is not None:
        permit = await session.scalar(select(OneTimeToken.id).where(
            OneTimeToken.user_id == user.id, OneTimeToken.purpose == "reenroll", OneTimeToken.used_at.is_(None),
            OneTimeToken.expires_at > datetime.now(UTC)).limit(1))
        if permit is None:
            return "reenroll"
    if not _in_networks(ip, cfg.portal_enroll_networks):
        return "network"
    allowed = {g.strip().lower() for g in cfg.portal_enroll_groups.split(",") if g.strip()}
    if allowed and not (allowed & await _groups(session, user)):
        return "group"
    return None


async def _state(session: AsyncSession, row: AdminSession, user: User, ip: str) -> dict:
    factors = await _factors(session, user)
    login_methods = [m for m, ok in (("totp", factors["totp"]), ("push", factors["devices"]), ("backup", factors["backup_codes_left"])) if ok]
    denied = None
    if row.mfa_passed:
        stage = "ready"
    elif login_methods:
        stage = "mfa"
    else:
        denied = await _enroll_denied(session, user, ip)
        stage = "enroll_denied" if denied else "enroll"
    out = {"login": user.login, "display_name": user.display_name, "source": user.source, "csrf": row.csrf, "stage": stage,
           "login_methods": login_methods, "methods": await _methods(session, user), "enroll_denied": denied,
           "expires_at": row.expires_at.isoformat()}
    if stage in ("ready", "enroll"):
        out["factors"] = factors
    return out


async def _session(request: Request, session: AsyncSession, *, stages: tuple[str, ...] | None = None) -> tuple[AdminSession, User]:
    found = await portal_session(request, session)
    if found is None:
        raise HTTPException(401, {"code": "unauthenticated", "message": "Сессия истекла, войдите заново"})
    row, user = found
    row = await session.merge(row)
    if request.method != "GET" and request.headers.get("x-csrf-token") != row.csrf:
        raise HTTPException(403, {"code": "csrf", "message": "Нет CSRF-токена"})
    if stages == ("ready",) and not row.mfa_passed:
        raise HTTPException(401, {"code": "mfa_required", "message": "Требуется второй фактор"})
    if stages and "enroll" in stages and not row.mfa_passed:
        state = await _state(session, row, user, client_ip(request))
        if state["stage"] != "enroll":
            raise HTTPException(403, {"code": "enroll_not_allowed", "message": "Настройка без второго фактора недоступна",
                                      "reason": state["enroll_denied"] or state["stage"]})
    elif row.mfa_passed and row.expires_at - datetime.now(UTC) < timedelta(minutes=settings().admin_session_minutes - 1):
        row.expires_at = datetime.now(UTC) + timedelta(minutes=settings().admin_session_minutes)   # скользящий срок
        await session.commit()
    return row, user


async def _confirmed(session: AsyncSession, row: AdminSession, user: User, ip: str, method: str) -> dict:
    row.mfa_passed = True
    row.expires_at = datetime.now(UTC) + timedelta(minutes=settings().admin_session_minutes)
    user.last_login_at, user.failed_attempts, user.locked_until = datetime.now(UTC), 0, None
    audit(session, "portal.login", actor=user.login, ip=ip, method=method)
    await session.commit()
    return await _state(session, row, user, ip)


# ------------------------------------------------------------------------------------------------------- вход
@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    ip = client_ip(request)
    await login_limiter.check(session, ip)
    user, reason = await authflow.primary(session, body.login, body.password)
    if not reason and user is not None:
        policies = (await session.scalars(select(Policy).where(Policy.enabled))).all()
        if evaluate(policies, None, await _groups(session, user)).action == "deny":
            reason = "policy_deny"
    if not reason and user is not None and user.must_change_password:
        reason = "password_change_required"
    if reason or user is None:
        audit(session, "portal.login", actor=body.login.strip().lower(), ip=ip, result="fail", severity="warning", reason=reason)
        await session.commit()
        message = {"policy_deny": "Доступ запрещён политикой",
                   "password_change_required": "Сначала смените временный пароль в панели управления"}.get(reason, authflow.GENERIC_PRIMARY)
        raise HTTPException(401, {"code": "bad_credentials", "message": message})
    token = new_token()
    row = AdminSession(id=sha256_hex(token), kind="portal", user_id=user.id, csrf=new_token(16), ip=ip,
                       expires_at=datetime.now(UTC) + timedelta(minutes=PENDING_MINUTES))
    session.add(row)
    await session.flush()
    state = await _state(session, row, user, ip)
    if state["stage"] == "enroll_denied":
        audit(session, "portal.enroll_denied", actor=user.login, ip=ip, result="fail", severity="warning", reason=state["enroll_denied"])
    await session.commit()
    response.set_cookie(PORTAL_COOKIE, token, httponly=True, secure=cookie_secure(request), samesite="strict", path="/")
    return state


@router.get("/me")
async def me(request: Request, session: AsyncSession = Depends(get_session)):
    row, user = await _session(request, session)
    return await _state(session, row, user, client_ip(request))


@router.post("/logout")
async def logout(request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    found = await portal_session(request, session)
    if found is not None:
        await session.delete(await session.merge(found[0]))
        await session.commit()
    response.delete_cookie(PORTAL_COOKIE, path="/")
    return {"ok": True}


@router.post("/mfa")
async def mfa(body: CodeIn, request: Request, session: AsyncSession = Depends(get_session)):
    """Код TOTP или резервный код."""
    ip = client_ip(request)
    await login_limiter.check(session, ip)
    row, user = await _session(request, session)
    check_lock(user)
    if await authflow.verify_totp(session, user, body.code):
        return await _confirmed(session, row, user, ip, "totp")
    now = datetime.now(UTC)
    used = await session.scalar(update(OneTimeToken).where(
        OneTimeToken.user_id == user.id, OneTimeToken.purpose == "backup_code", # shortcut: принимаем и sha256 кодов 0.3.0; убрать, когда они выйдут из оборота
        OneTimeToken.token_hash.in_((_backup_hash(user, body.code), sha256_hex(_norm_code(body.code)))),
        OneTimeToken.used_at.is_(None)).values(used_at=now).returning(OneTimeToken.id))
    if used:
        audit(session, "portal.backup_code_used", actor=user.login, ip=ip, severity="warning")
        return await _confirmed(session, row, user, ip, "backup_code")
    await authflow._record_failure(session, user)
    audit(session, "portal.mfa", actor=user.login, ip=ip, result="fail", severity="warning")
    await session.commit()
    raise HTTPException(401, {"code": "invalid_otp", "message": "Неверный или уже использованный код"})


@router.post("/mfa/push")
async def mfa_push(request: Request, session: AsyncSession = Depends(get_session)):
    await login_limiter.check(session, client_ip(request))
    row, user = await _session(request, session)
    check_lock(user)
    return await session_push(session, row, user, client_ip(request), "portal", TITLE)


@router.get("/mfa/push/{request_id}")
async def mfa_push_status(request_id: uuid.UUID, request: Request, wait: int = Query(0, ge=0, le=25),
                          session: AsyncSession = Depends(get_session)):
    row, user = await _session(request, session)
    status = await session_push_result(session, row, user, request_id, "portal", wait)
    if status == "approved":
        return {"status": "approved", "me": await _confirmed(session, row, user, client_ip(request), "push")}
    return {"status": status}


# ------------------------------------------------------------------------------------------------ свои факторы
class TotpConfirmIn(BaseModel):
    code: str = Field(min_length=6, max_length=8)
    name: str = Field("Приложение-аутентификатор", min_length=1, max_length=128)


async def _need_method(session: AsyncSession, user: User, method: str) -> None:
    if method not in await _methods(session, user):
        raise HTTPException(403, {"code": "method_not_allowed", "message": "Этот способ запрещён политикой для вашей группы"})


@router.post("/totp")
async def totp_start(request: Request, session: AsyncSession = Depends(get_session)):
    row, user = await _session(request, session, stages=("ready", "enroll"))
    await _need_method(session, user, "totp")
    # незавершённые попытки не копятся: новый QR заменяет прежний неподтверждённый
    await session.execute(delete(TotpFactor).where(TotpFactor.user_id == user.id, TotpFactor.status == "pending"))
    factor = TotpFactor(id=uuid.uuid4(), user_id=user.id, name="Приложение-аутентификатор", status="pending", secret_enc="")
    secret = totp.new_secret()
    factor.secret_enc = box().encrypt(secret, aad=b"totp:" + str(factor.id).encode())
    session.add(factor)
    await session.commit()
    uri = totp.otpauth_uri(settings().server_name, user.login, secret)
    return {"factor_id": str(factor.id), "otpauth": uri, "secret": secret, "qr_svg": totp.qr_svg(uri)}


@router.post("/totp/{factor_id}/confirm")
async def totp_confirm(factor_id: uuid.UUID, body: TotpConfirmIn, request: Request, session: AsyncSession = Depends(get_session)):
    row, user = await _session(request, session, stages=("ready", "enroll"))
    factor = await session.get(TotpFactor, factor_id)
    if factor is None or factor.user_id != user.id or factor.status != "pending":
        raise HTTPException(404, {"code": "not_found", "message": "Настройка не найдена — начните заново"})
    step = totp.verify(box().decrypt(factor.secret_enc, aad=b"totp:" + str(factor.id).encode()), body.code)
    if step is None:
        raise HTTPException(400, {"code": "invalid_otp", "message": "Код не подошёл — проверьте время на телефоне"})
    factor.status, factor.last_step, factor.name = "active", step, body.name
    await authflow.factor_activated(session, user)
    audit(session, "totp.enrolled", actor=user.login, target=str(factor.id), ip=client_ip(request), via="portal")
    if not row.mfa_passed:   # первый фактор: код только что подтверждён — это и есть второй фактор этой сессии
        return await _confirmed(session, row, user, client_ip(request), "totp_setup")
    await session.commit()
    return await _state(session, row, user, client_ip(request))


async def _not_last(session: AsyncSession, user: User) -> None:
    f = await _factors(session, user)
    if len(f["totp"]) + len(f["devices"]) <= 1:
        raise HTTPException(409, {"code": "last_factor", "message": "Это последний способ входа — сначала подключите другой"})


@router.delete("/totp/{factor_id}")
async def totp_delete(factor_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)):
    row, user = await _session(request, session, stages=("ready",))
    factor = await session.get(TotpFactor, factor_id)
    if factor is None or factor.user_id != user.id or factor.status != "active":
        raise HTTPException(404, {"code": "not_found", "message": "Способ не найден"})
    await _not_last(session, user)
    await session.delete(factor)
    audit(session, "totp.revoked", actor=user.login, target=str(factor_id), ip=client_ip(request), severity="warning", via="portal")
    await session.commit()
    return await _state(session, row, user, client_ip(request))


@router.post("/devices/enrollment")
async def device_enrollment(request: Request, session: AsyncSession = Depends(get_session)):
    row, user = await _session(request, session, stages=("ready", "enroll"))
    await _need_method(session, user, "push")
    return await issue_enrollment(session, user, user.login, client_ip(request), timedelta(minutes=PENDING_MINUTES))


@router.post("/devices/{device_id}/revoke")
async def device_revoke(device_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)):
    row, user = await _session(request, session, stages=("ready",))
    device = await session.get(Device, device_id)
    if device is None or device.user_id != user.id or device.status != "active":
        raise HTTPException(404, {"code": "not_found", "message": "Устройство не найдено"})
    await _not_last(session, user)
    device.status, device.push_token_enc = "revoked", None
    audit(session, "device.revoked", actor=user.login, target=str(device.id), ip=client_ip(request), severity="warning", via="portal")
    await session.commit()
    return await _state(session, row, user, client_ip(request))


@router.post("/backup-codes")
async def backup_codes(request: Request, session: AsyncSession = Depends(get_session)):
    """Новый набор резервных кодов; прежние неиспользованные перестают действовать. Коды показываются один раз."""
    row, user = await _session(request, session, stages=("ready",))
    await session.execute(delete(OneTimeToken).where(OneTimeToken.user_id == user.id, OneTimeToken.purpose == "backup_code"))
    codes = new_backup_codes()
    expires = datetime.now(UTC) + timedelta(days=3650)
    for code in codes:
        session.add(OneTimeToken(user_id=user.id, purpose="backup_code", token_hash=_backup_hash(user, code), expires_at=expires,
                                 created_by=user.login))
    audit(session, "portal.backup_codes_issued", actor=user.login, ip=client_ip(request))
    await session.commit()
    return {"codes": codes}


# ------------------------------------------------------------------------------------------ история и сеансы
@router.get("/history")
async def history(request: Request, session: AsyncSession = Depends(get_session)):
    _, user = await _session(request, session, stages=("ready",))
    rows = (await session.scalars(select(AuthRequest).where(AuthRequest.user_id == user.id)
                                  .order_by(AuthRequest.created_at.desc()).limit(50))).all()
    return {"items": [{"id": str(r.id), "at": r.created_at.isoformat(), "resource": r.resource_name, "channel": r.channel,
                       "method": r.method, "status": r.status, "reason": authflow.public_message(r.reason) if r.reason else None,
                       "source_ip": r.source_ip} for r in rows]}


@router.get("/sessions")
async def sessions(request: Request, session: AsyncSession = Depends(get_session)):
    row, user = await _session(request, session, stages=("ready",))
    rows = (await session.scalars(select(AdminSession).where(
        AdminSession.user_id == user.id, AdminSession.kind == "portal", AdminSession.mfa_passed,
        AdminSession.expires_at > datetime.now(UTC)).order_by(AdminSession.created_at.desc()))).all()
    return {"items": [{"id": r.id[:16], "ip": r.ip, "created_at": r.created_at.isoformat(), "current": r.id == row.id} for r in rows]}


@router.delete("/sessions/{session_id}")
async def end_session(session_id: str, request: Request, session: AsyncSession = Depends(get_session)):
    row, user = await _session(request, session, stages=("ready",))
    if len(session_id) != 16 or set(session_id) - set("0123456789abcdef") or row.id.startswith(session_id):
        raise HTTPException(404, {"code": "not_found", "message": "Сеанс не найден"})
    res = await session.execute(delete(AdminSession).where(AdminSession.user_id == user.id, AdminSession.kind == "portal",
                                                           AdminSession.id.startswith(session_id)))
    audit(session, "portal.session_closed", actor=user.login, ip=client_ip(request))
    await session.commit()
    if not res.rowcount:
        raise HTTPException(404, {"code": "not_found", "message": "Сеанс не найден"})
    return {"ok": True}
