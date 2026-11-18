"""Вход администратора (второй фактор обязателен всегда, политики его не отменяют) и интеграционный API запросов доступа."""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session, sessionmaker
from ..models import AdminSession, AuthRequest, Device, OneTimeToken, Resource, TotpFactor, User
from ..security import totp
from ..security.crypto import box, hash_password, new_token, sha256_hex, verify_password
from ..services import authflow
from ..services.audit import audit
from .deps import ROLE_LABELS, ROLES, SESSION_COOKIE, Principal, admin_session, client_ip, cookie_secure, login_limiter, need

router = APIRouter(prefix="/api/v1", tags=["auth"])


class LoginIn(BaseModel):
    login: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=64)


def _me(user: User, row: AdminSession) -> dict:
    return {"login": user.login, "display_name": user.display_name, "role": user.admin_role, "role_label": ROLE_LABELS.get(user.admin_role or ""),
            "permissions": sorted(ROLES.get(user.admin_role or "", set())), "csrf": row.csrf, "mfa_passed": row.mfa_passed,
            "must_change_password": user.must_change_password, "source": user.source}


async def _pending_session(request: Request, session: AsyncSession) -> tuple[AdminSession, User]:
    found = await admin_session(request, session)
    if found is None:
        raise HTTPException(401, {"code": "unauthenticated", "message": "Сессия истекла, войдите заново"})
    row, user = found
    row = await session.merge(row)
    return row, user


@router.post("/auth/login")
async def login(body: LoginIn, request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    ip = client_ip(request)
    await login_limiter.check(session, ip)
    user, reason = await authflow.primary(session, body.login, body.password)
    if reason or user is None or not user.admin_role:
        audit(session, "admin.login", actor=body.login.strip().lower(), ip=ip, result="fail", severity="warning",
              reason=reason or "not_admin")
        await session.commit()
        raise HTTPException(401, {"code": "bad_credentials", "message": authflow.GENERIC_PRIMARY})
    token = new_token()
    row = AdminSession(id=sha256_hex(token), user_id=user.id, csrf=new_token(16), ip=ip,
                       expires_at=datetime.now(UTC) + timedelta(minutes=10))   # до прохождения MFA — короткая сессия
    session.add(row)
    has_totp = await session.scalar(select(TotpFactor.id).where(TotpFactor.user_id == user.id, TotpFactor.status == "active").limit(1))
    has_push = await session.scalar(select(Device.id).where(Device.user_id == user.id, Device.status == "active").limit(1))
    await session.commit()
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=cookie_secure(request), samesite="strict", path="/")
    methods = [m for m, ok in (("totp", has_totp), ("push", has_push)) if ok]
    nxt = "password_change" if user.must_change_password else ("mfa" if methods else "totp_setup")
    return {"next": nxt, "methods": methods, "csrf": row.csrf}


def _require_password_changed(user: User) -> None:
    if user.must_change_password:
        raise HTTPException(403, {"code": "password_change_required", "message": "Сначала смените временный пароль"})


class PasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


@router.post("/auth/password")
async def change_password(body: PasswordIn, request: Request, session: AsyncSession = Depends(get_session)):
    """Смена собственного пароля (локальная учётная запись). До второго фактора — только для временного пароля."""
    await login_limiter.check(session, client_ip(request))
    row, user = await _pending_session(request, session)
    if not row.mfa_passed and not user.must_change_password:
        raise HTTPException(401, {"code": "mfa_required", "message": "Требуется второй фактор"})
    if row.mfa_passed and request.headers.get("x-csrf-token") != row.csrf:
        raise HTTPException(403, {"code": "csrf", "message": "Нет CSRF-токена"})
    if user.source != "local":
        raise HTTPException(422, {"code": "not_local", "message": "Пароль доменной учётной записи меняется в каталоге"})
    if not await asyncio.to_thread(verify_password, body.current_password, user.password_hash):
        await authflow._record_failure(session, user)
        await session.commit()
        raise HTTPException(400, {"code": "bad_password", "message": "Текущий пароль указан неверно"})
    if body.new_password == body.current_password:
        raise HTTPException(422, {"code": "same_password", "message": "Новый пароль должен отличаться от текущего"})
    user.password_hash, user.must_change_password = await asyncio.to_thread(hash_password, body.new_password), False
    # остальные сессии этого пользователя закрываются: старый пароль мог быть известен
    await session.execute(delete(AdminSession).where(AdminSession.user_id == user.id, AdminSession.id != row.id))
    audit(session, "admin.password_changed", actor=user.login, ip=client_ip(request), temporary=not row.mfa_passed)
    await session.commit()
    if row.mfa_passed:
        return {"ok": True}
    has_totp = await session.scalar(select(TotpFactor.id).where(TotpFactor.user_id == user.id, TotpFactor.status == "active").limit(1))
    has_push = await session.scalar(select(Device.id).where(Device.user_id == user.id, Device.status == "active").limit(1))
    methods = [m for m, ok in (("totp", has_totp), ("push", has_push)) if ok]
    return {"next": "mfa" if methods else "totp_setup", "methods": methods, "csrf": row.csrf}


async def _mfa_ok(session: AsyncSession, row: AdminSession, user: User, ip: str, method: str) -> dict:
    row.mfa_passed = True
    row.expires_at = datetime.now(UTC) + timedelta(minutes=settings().admin_session_minutes)
    user.last_login_at, user.failed_attempts, user.locked_until = datetime.now(UTC), 0, None
    audit(session, "admin.login", actor=user.login, ip=ip, method=method)
    await session.commit()
    return _me(user, row)


@router.post("/auth/mfa")
async def mfa(body: CodeIn, request: Request, session: AsyncSession = Depends(get_session)):
    """TOTP-код или аварийный одноразовый код (выдаётся только командой mfagatectl emergency на сервере)."""
    await login_limiter.check(session, client_ip(request))
    row, user = await _pending_session(request, session)
    _require_password_changed(user)
    if user.locked_until and user.locked_until > datetime.now(UTC):   # иначе код можно перебирать, заводя новые сессии
        raise HTTPException(401, {"code": "locked", "message": "Слишком много ошибок, вход временно заблокирован"})
    if await authflow.verify_totp(session, user, body.code):
        return await _mfa_ok(session, row, user, client_ip(request), "totp")
    now = datetime.now(UTC)
    used = await session.scalar(update(OneTimeToken).where(
        OneTimeToken.user_id == user.id, OneTimeToken.purpose == "emergency_admin", OneTimeToken.token_hash == sha256_hex(body.code.strip()),
        OneTimeToken.used_at.is_(None), OneTimeToken.expires_at > now).values(used_at=now).returning(OneTimeToken.id))
    if used:
        audit(session, "admin.emergency_access", actor=user.login, ip=client_ip(request), severity="critical")
        return await _mfa_ok(session, row, user, client_ip(request), "emergency")
    await authflow._record_failure(session, user)
    audit(session, "admin.mfa", actor=user.login, ip=client_ip(request), result="fail", severity="warning")
    await session.commit()
    raise HTTPException(401, {"code": "invalid_otp", "message": "Неверный или уже использованный код"})


@router.post("/auth/mfa/push")
async def mfa_push(request: Request, session: AsyncSession = Depends(get_session)):
    row, user = await _pending_session(request, session)
    _require_password_changed(user)
    devices = (await session.scalars(select(Device).where(Device.user_id == user.id, Device.status == "active"))).all()
    if not devices:
        raise HTTPException(400, {"code": "no_factor", "message": "Нет привязанных устройств"})
    req = AuthRequest(login=user.login, channel="admin", client_name="Панель управления", source_ip=client_ip(request),
                      resource_name="Панель управления MFA Gate", status="pending", session_ref=row.id)
    await authflow.start_push(session, user, req, devices)
    await session.commit()
    return {"request_id": str(req.id), "expires_at": req.expires_at.isoformat()}


@router.get("/auth/mfa/push/{request_id}")
async def mfa_push_status(request_id: uuid.UUID, request: Request, wait: int = Query(0, ge=0, le=25),
                          session: AsyncSession = Depends(get_session)):
    row, user = await _pending_session(request, session)
    await session.commit()   # не держать соединение из пула на время ожидания
    req = await authflow.wait_decision(sessionmaker(), request_id, time.monotonic() + wait)
    if req is None or req.user_id != user.id or req.channel != "admin" or req.session_ref != row.id:
        raise HTTPException(404, {"code": "not_found", "message": "Запрос не найден"})
    if req.status == "approved":
        # одобрение одноразовое: гасим атомарно, повторный опрос (или другая сессия) его не используют
        used = await session.scalar(update(AuthRequest).where(AuthRequest.id == req.id, AuthRequest.status == "approved")
                                    .values(status="consumed").returning(AuthRequest.id))
        if used:
            return {"status": "approved", "me": await _mfa_ok(session, row, user, client_ip(request), "push")}
        return {"status": "consumed"}
    return {"status": req.status}


async def _require_no_factor(session: AsyncSession, user: User) -> None:
    """Первичная настройка в сессии «только пароль» допустима, лишь пока у администратора нет НИ ОДНОГО фактора —
    иначе знание пароля позволило бы подменить второй фактор (например, у входящего только через телефон)."""
    if (await session.scalar(select(TotpFactor.id).where(TotpFactor.user_id == user.id, TotpFactor.status == "active").limit(1))
            or await session.scalar(select(Device.id).where(Device.user_id == user.id, Device.status == "active").limit(1))):
        raise HTTPException(409, {"code": "already_enrolled", "message": "Второй фактор уже подключён — войдите с ним"})


@router.post("/auth/totp-setup")
async def totp_setup(request: Request, session: AsyncSession = Depends(get_session)):
    """Первичное подключение TOTP администратором без факторов. Доступно только в неподтверждённой сессии после пароля."""
    row, user = await _pending_session(request, session)
    _require_password_changed(user)
    await _require_no_factor(session, user)
    factor = TotpFactor(id=uuid.uuid4(), user_id=user.id, name="Вход в панель", status="pending", secret_enc="")
    secret = totp.new_secret()
    factor.secret_enc = box().encrypt(secret, aad=b"totp:" + str(factor.id).encode())
    session.add(factor)
    await session.commit()
    uri = totp.otpauth_uri(settings().server_name, user.login, secret)
    return {"factor_id": str(factor.id), "otpauth": uri, "secret": secret, "qr_svg": totp.qr_svg(uri)}


@router.post("/auth/totp-setup/{factor_id}")
async def totp_setup_confirm(factor_id: uuid.UUID, body: CodeIn, request: Request, session: AsyncSession = Depends(get_session)):
    row, user = await _pending_session(request, session)
    _require_password_changed(user)
    await _require_no_factor(session, user)
    factor = await session.get(TotpFactor, factor_id)
    if factor is None or factor.user_id != user.id or factor.status != "pending":
        raise HTTPException(404, {"code": "not_found", "message": "Фактор не найден"})
    secret = box().decrypt(factor.secret_enc, aad=b"totp:" + str(factor.id).encode())
    step = totp.verify(secret, body.code)
    if step is None:
        raise HTTPException(400, {"code": "invalid_otp", "message": "Код не подошёл — проверьте время на телефоне"})
    factor.status, factor.last_step = "active", step
    audit(session, "totp.enrolled", actor=user.login, target=str(factor.id), ip=client_ip(request))
    return await _mfa_ok(session, row, user, client_ip(request), "totp_setup")


@router.get("/auth/me")
async def me(request: Request, session: AsyncSession = Depends(get_session)):
    found = await admin_session(request, session)
    if found is None:
        raise HTTPException(401, {"code": "unauthenticated", "message": "Требуется вход"})
    row, user = found
    return _me(user, row)


@router.post("/auth/logout")
async def logout(request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    found = await admin_session(request, session)
    if found is not None:
        await session.delete(await session.merge(found[0]))
        await session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


# ------------------------------------------------------------------------------------------- интеграционный API
class AccessRequestIn(BaseModel):
    login: str = Field(min_length=1, max_length=255)
    password: str | None = Field(None, max_length=1024, description="Не передавать, если первый фактор проверил шлюз")
    otp: str | None = Field(None, max_length=16)
    resource_id: uuid.UUID | None = None
    source_ip: str | None = Field(None, max_length=64)
    client_name: str | None = Field(None, max_length=255)


STATUS_MAP = {"approved": "accepted", "allowed": "accepted", "pending": "pending", "denied": "rejected", "rejected": "rejected",
              "expired": "rejected"}


def _ar(req: AuthRequest) -> dict:
    status = STATUS_MAP.get(req.status, "rejected")
    if status == "pending" and req.method == "totp":
        status = "challenge"
    return {"id": str(req.id), "status": status, "reason": req.reason, "message": authflow.REASONS.get(req.reason or "", ""),
            "method": req.method, "expires_at": req.expires_at.isoformat() if req.expires_at else None}


@router.post("/access-requests", tags=["integration"])
async def create_access_request(body: AccessRequestIn, p: Principal = Depends(need("auth:write")),
                                session: AsyncSession = Depends(get_session)):
    bound = p.api_client.resource_id if p.api_client else None
    if bound and body.resource_id and body.resource_id != bound:
        raise HTTPException(403, {"code": "resource_mismatch", "message": "Ключ привязан к другому ресурсу"})
    resource_id = bound or body.resource_id
    resource = await session.get(Resource, resource_id) if resource_id else None
    if resource_id and resource is None:
        raise HTTPException(404, {"code": "not_found", "message": "Ресурс не найден"})
    outcome = await authflow.begin(session, login=body.login, password=body.password, otp=body.otp, resource=resource, channel="api",
                                   client_name=body.client_name or (p.api_client.name if p.api_client else p.name),
                                   source_ip=body.source_ip, api_client_id=p.api_client.id if p.api_client else None)
    await session.commit()
    req = await session.get(AuthRequest, outcome.request_id)
    return _ar(req)


async def _own(session: AsyncSession, request_id: uuid.UUID, p: Principal) -> AuthRequest:
    req = await session.get(AuthRequest, request_id)
    if req is None or req.channel != "api" or (p.api_client and req.api_client_id != p.api_client.id):
        raise HTTPException(404, {"code": "not_found", "message": "Запрос не найден"})
    return req


@router.get("/access-requests/{request_id}", tags=["integration"])
async def access_request_status(request_id: uuid.UUID, wait: int = Query(0, ge=0, le=30), p: Principal = Depends(need("auth:write")),
                                session: AsyncSession = Depends(get_session)):
    await _own(session, request_id, p)
    await session.commit()   # не держать соединение из пула на время ожидания
    req = await authflow.wait_decision(sessionmaker(), request_id, time.monotonic() + wait)
    return _ar(req)


@router.post("/access-requests/{request_id}/otp", tags=["integration"])
async def access_request_otp(request_id: uuid.UUID, body: CodeIn, p: Principal = Depends(need("auth:write")),
                             session: AsyncSession = Depends(get_session)):
    await _own(session, request_id, p)
    await authflow.answer_challenge(session, request_id, body.code)
    await session.commit()
    return _ar(await session.get(AuthRequest, request_id))
