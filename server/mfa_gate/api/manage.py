"""Управляющий API v1: используется веб-панелью (сессия администратора) и интеграциями (ключ API со scopes)."""
from __future__ import annotations

import asyncio
import csv
import io
import ipaddress
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..models import (ApiClient, AuditEvent, AuthRequest, Device, Group, LdapDirectory, OneTimeToken, Policy, RadiusClient, Resource,
                      TotpFactor, User)
from ..security import totp
from ..security.crypto import b64url, box, hash_password, new_token, sha256_hex
from ..services import authflow
from ..services.audit import audit
from .deps import AREAS, ROLE_LABELS, Principal, need

router = APIRouter(prefix="/api/v1")
NOT_FOUND = {"code": "not_found", "message": "Объект не найден"}


def _404(obj):
    if obj is None:
        raise HTTPException(404, NOT_FOUND)
    return obj


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def _page(session: AsyncSession, stmt: Select, page: int, size: int) -> tuple[list, int]:
    total = await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = (await session.scalars(stmt.offset((page - 1) * size).limit(size))).all()
    return list(rows), total or 0


def _cidr(value: str) -> str:
    try:
        return str(ipaddress.ip_network(value.strip(), strict=False))
    except ValueError:
        raise ValueError(f"не IP-адрес и не сеть: {value}") from None


# ------------------------------------------------------------------------------------------------ пользователи
class UserIn(BaseModel):
    login: str = Field(min_length=1, max_length=255, pattern=r"^[^\s/\\]+$")
    source: Literal["local", "ldap"] = "local"
    display_name: str = Field("", max_length=255)
    email: str | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=64)
    password: str | None = Field(None, min_length=12, max_length=1024)
    admin_role: Literal["superadmin", "admin", "operator", "auditor"] | None = None
    group_ids: list[uuid.UUID] = []


class UserPatch(BaseModel):
    display_name: str | None = Field(None, max_length=255)
    email: str | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=64)
    status: Literal["active", "disabled"] | None = None
    admin_role: Literal["superadmin", "admin", "operator", "auditor", ""] | None = None
    group_ids: list[uuid.UUID] | None = None
    password: str | None = Field(None, min_length=12, max_length=1024)


def user_out(u: User, *, devices: int | None = None, totp_count: int | None = None) -> dict:
    return {"id": str(u.id), "login": u.login, "display_name": u.display_name, "email": u.email, "phone": u.phone, "source": u.source,
            "status": u.status, "admin_role": u.admin_role, "admin_role_label": ROLE_LABELS.get(u.admin_role or ""),
            "groups": [{"id": str(g.id), "name": g.name} for g in u.groups], "directory_groups": u.directory_groups or [],
            "locked_until": _dt(u.locked_until) if u.locked_until and u.locked_until > datetime.now(UTC) else None,
            "last_login_at": _dt(u.last_login_at), "created_at": _dt(u.created_at), "devices": devices, "totp": totp_count}


async def _set_groups(session: AsyncSession, user: User, ids: list[uuid.UUID]) -> None:
    user.groups = list((await session.scalars(select(Group).where(Group.id.in_(ids)))).all()) if ids else []


def _guard_admin_target(p: Principal, user: User) -> None:
    """Учётные записи администраторов меняет только главный администратор: иначе оператор сменил бы пароль или выпустил
    себе TOTP/привязку телефона суперадмина и вошёл под ним."""
    if user.admin_role and "*" not in p.perms and "admins:write" not in p.perms:
        raise HTTPException(403, {"code": "forbidden", "message": "Учётные записи администраторов меняет только главный администратор"})


async def _admin_target(session: AsyncSession, p: Principal, user_id: uuid.UUID) -> User:
    user = _404(await session.get(User, user_id))
    _guard_admin_target(p, user)
    return user


async def _guard_last_superadmin(session: AsyncSession, user: User) -> None:
    if user.admin_role == "superadmin":
        others = await session.scalar(select(func.count()).select_from(User).where(
            User.admin_role == "superadmin", User.status == "active", User.id != user.id))
        if not others:
            raise HTTPException(409, {"code": "last_superadmin", "message": "Нельзя лишить прав последнего главного администратора"})


@router.get("/users", tags=["users"])
async def list_users(q: str = "", source: str | None = None, page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200),
                     _: Principal = Depends(need("users:read")), session: AsyncSession = Depends(get_session)):
    stmt = select(User).order_by(User.login)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(User.login.like(like), func.lower(User.display_name).like(like), func.lower(User.email).like(like)))
    if source in ("local", "ldap"):
        stmt = stmt.where(User.source == source)
    rows, total = await _page(session, stmt, page, size)
    ids = [u.id for u in rows]
    dev = dict((await session.execute(select(Device.user_id, func.count()).where(Device.user_id.in_(ids), Device.status == "active")
                                      .group_by(Device.user_id))).all())
    tot = dict((await session.execute(select(TotpFactor.user_id, func.count()).where(TotpFactor.user_id.in_(ids), TotpFactor.status == "active")
                                      .group_by(TotpFactor.user_id))).all())
    return {"items": [user_out(u, devices=dev.get(u.id, 0), totp_count=tot.get(u.id, 0)) for u in rows], "total": total}


@router.post("/users", tags=["users"], status_code=201)
async def create_user(body: UserIn, p: Principal = Depends(need("users:write")), session: AsyncSession = Depends(get_session)):
    if body.admin_role and "*" not in p.perms and "admins:write" not in p.perms:
        raise HTTPException(403, {"code": "forbidden", "message": "Назначать роли может только главный администратор"})
    login = body.login.strip().lower()
    if await session.scalar(select(User.id).where(User.login == login)):
        raise HTTPException(409, {"code": "exists", "message": "Пользователь с таким логином уже есть"})
    groups = list((await session.scalars(select(Group).where(Group.id.in_(body.group_ids)))).all()) if body.group_ids else []
    user = User(login=login, source=body.source, display_name=body.display_name, email=body.email, phone=body.phone,
                admin_role=body.admin_role, directory_groups=[], groups=groups)
    if body.source == "local":
        if not body.password:
            raise HTTPException(422, {"code": "password_required", "message": "Для локального пользователя нужен пароль (от 12 символов)"})
        user.password_hash = await asyncio.to_thread(hash_password, body.password)
    else:   # заранее заводим доменного пользователя, чтобы привязать устройство до первого входа
        from ..services import ldap as ldap_service   # noqa: PLC0415
        directories = (await session.scalars(select(LdapDirectory).where(LdapDirectory.enabled).order_by(LdapDirectory.priority))).all()
        found = None
        for directory in directories:
            try:
                found = await ldap_service.lookup(directory, login)
            except ldap_service.LdapError:
                continue
            if found:
                user.directory_id, user.external_id = directory.id, found.external_id
                user.display_name = body.display_name or found.display_name
                user.email, user.phone, user.directory_groups = body.email or found.email, body.phone or found.phone, found.groups
                user.login = found.login.lower()
                break
        if found is None:
            raise HTTPException(404, {"code": "not_in_directory", "message": "Пользователь не найден ни в одном каталоге"})
    session.add(user)
    audit(session, "user.created", actor=p.name, target=user.login, ip=p.ip, source=user.source, admin_role=user.admin_role)
    await session.commit()
    return user_out(user)


@router.get("/users/{user_id}", tags=["users"])
async def get_user(user_id: uuid.UUID, _: Principal = Depends(need("users:read")), session: AsyncSession = Depends(get_session)):
    user = _404(await session.get(User, user_id))
    devices = (await session.scalars(select(Device).where(Device.user_id == user.id).order_by(Device.created_at.desc()))).all()
    factors = (await session.scalars(select(TotpFactor).where(TotpFactor.user_id == user.id).order_by(TotpFactor.created_at))).all()
    requests = (await session.scalars(select(AuthRequest).where(AuthRequest.user_id == user.id)
                                      .order_by(AuthRequest.created_at.desc()).limit(20))).all()
    now = datetime.now(UTC)
    backup = await session.scalar(select(func.count()).select_from(OneTimeToken).where(
        OneTimeToken.user_id == user.id, OneTimeToken.purpose == "backup_code", OneTimeToken.used_at.is_(None)))
    permit = await session.scalar(select(func.max(OneTimeToken.expires_at)).where(
        OneTimeToken.user_id == user.id, OneTimeToken.purpose == "reenroll", OneTimeToken.used_at.is_(None), OneTimeToken.expires_at > now))
    return {**user_out(user), "device_list": [device_out(d) for d in devices],
            "mfa_enrolled_at": _dt(user.mfa_enrolled_at), "backup_codes_left": backup or 0, "reenroll_until": _dt(permit),
            "totp_list": [{"id": str(f.id), "name": f.name, "status": f.status, "last_used_at": _dt(f.last_used_at),
                           "created_at": _dt(f.created_at)} for f in factors],
            "requests": [request_out(r) for r in requests]}


@router.patch("/users/{user_id}", tags=["users"])
async def update_user(user_id: uuid.UUID, body: UserPatch, p: Principal = Depends(need("users:write")),
                      session: AsyncSession = Depends(get_session)):
    user = await _admin_target(session, p, user_id)
    changes = body.model_dump(exclude_unset=True)
    if "admin_role" in changes and (changes["admin_role"] or None) != user.admin_role:
        if "*" not in p.perms and "admins:write" not in p.perms:
            raise HTTPException(403, {"code": "forbidden", "message": "Менять роли может только главный администратор"})
        await _guard_last_superadmin(session, user)
        user.admin_role = changes["admin_role"] or None
    if changes.get("status") == "disabled":
        await _guard_last_superadmin(session, user)
    for f in ("display_name", "email", "phone", "status"):
        if f in changes and changes[f] is not None:
            setattr(user, f, changes[f])
    if body.password:
        if user.source != "local":
            raise HTTPException(422, {"code": "not_local", "message": "Пароль доменного пользователя меняется в каталоге"})
        user.password_hash = await asyncio.to_thread(hash_password, body.password)
    if body.group_ids is not None:
        await _set_groups(session, user, body.group_ids)
    audit(session, "user.updated", actor=p.name, target=user.login, ip=p.ip, fields=sorted(k for k in changes if k != "password"),
          password_changed=bool(body.password))
    await session.commit()
    return user_out(user)


@router.post("/users/{user_id}/unlock", tags=["users"])
async def unlock_user(user_id: uuid.UUID, p: Principal = Depends(need("users:write")), session: AsyncSession = Depends(get_session)):
    user = await _admin_target(session, p, user_id)
    user.failed_attempts, user.locked_until = 0, None
    audit(session, "user.unlocked", actor=p.name, target=user.login, ip=p.ip)
    await session.commit()
    return user_out(user)


@router.post("/users/{user_id}/reenroll", tags=["users"])
async def allow_reenroll(user_id: uuid.UUID, p: Principal = Depends(need("devices:write")), session: AsyncSession = Depends(get_session)):
    """Восстановление после потери телефона: на 24 ч сотрудник может сам настроить фактор в портале по одному паролю
    (по-прежнему только из сетей регистрации). Разрешение гаснет при подтверждении нового фактора."""
    user = await _admin_target(session, p, user_id)
    expires = datetime.now(UTC) + timedelta(hours=24)
    session.add(OneTimeToken(user_id=user.id, purpose="reenroll", token_hash=sha256_hex(new_token()), expires_at=expires, created_by=p.name))
    audit(session, "user.reenroll_allowed", actor=p.name, target=user.login, ip=p.ip, severity="warning")
    await session.commit()
    return {"reenroll_until": _dt(expires)}


@router.delete("/users/{user_id}/backup-codes", tags=["users"], status_code=204)
async def reset_backup_codes(user_id: uuid.UUID, p: Principal = Depends(need("devices:write")), session: AsyncSession = Depends(get_session)):
    user = await _admin_target(session, p, user_id)
    await session.execute(delete(OneTimeToken).where(OneTimeToken.user_id == user.id, OneTimeToken.purpose == "backup_code"))
    audit(session, "user.backup_codes_reset", actor=p.name, target=user.login, ip=p.ip, severity="warning")
    await session.commit()


@router.delete("/users/{user_id}", tags=["users"], status_code=204)
async def delete_user(user_id: uuid.UUID, p: Principal = Depends(need("users:write")), session: AsyncSession = Depends(get_session)):
    user = await _admin_target(session, p, user_id)
    await _guard_last_superadmin(session, user)
    audit(session, "user.deleted", actor=p.name, target=user.login, ip=p.ip, severity="warning")
    await session.delete(user)
    await session.commit()


@router.post("/users/{user_id}/enrollment", tags=["devices"])
async def device_enrollment(user_id: uuid.UUID, p: Principal = Depends(need("devices:write")), session: AsyncSession = Depends(get_session)):
    """Одноразовая ссылка привязки телефона: показывается QR-кодом, на сервере хранится только хэш."""
    user = await _admin_target(session, p, user_id)
    return await issue_enrollment(session, user, p.name, p.ip, timedelta(hours=settings().enrollment_ttl_hours))


async def issue_enrollment(session: AsyncSession, user: User, actor: str, ip: str, ttl: timedelta) -> dict:
    """Ссылка ведёт на внешний адрес Device API: телефон привязывается и подтверждает входы из дома, до подключения VPN."""
    token = new_token()
    expires = datetime.now(UTC) + ttl
    session.add(OneTimeToken(user_id=user.id, purpose="enroll_device", token_hash=sha256_hex(token), expires_at=expires, created_by=actor))
    audit(session, "device.enrollment_issued", actor=actor, target=user.login, ip=ip)
    await session.commit()
    cfg = settings()
    uri = (f"mfagate://enroll?v=1&url={b64url(cfg.device_url.encode())}&token={token}"
           f"&label={b64url(f'{user.login}@{cfg.server_name}'.encode())}")
    return {"uri": uri, "qr_svg": totp.qr_svg(uri), "expires_at": _dt(expires)}


@router.post("/users/{user_id}/totp", tags=["devices"])
async def totp_issue(user_id: uuid.UUID, p: Principal = Depends(need("devices:write")), session: AsyncSession = Depends(get_session)):
    user = await _admin_target(session, p, user_id)
    factor = TotpFactor(id=uuid.uuid4(), user_id=user.id, name="Аутентификатор", status="pending", secret_enc="")
    secret = totp.new_secret()
    factor.secret_enc = box().encrypt(secret, aad=b"totp:" + str(factor.id).encode())
    session.add(factor)
    audit(session, "totp.issued", actor=p.name, target=user.login, ip=p.ip)
    await session.commit()
    uri = totp.otpauth_uri(settings().server_name, user.login, secret)
    return {"factor_id": str(factor.id), "otpauth": uri, "secret": secret, "qr_svg": totp.qr_svg(uri)}


class TotpConfirm(BaseModel):
    code: str = Field(min_length=6, max_length=8)
    name: str = Field("Аутентификатор", max_length=128)


@router.post("/users/{user_id}/totp/{factor_id}/confirm", tags=["devices"])
async def totp_confirm(user_id: uuid.UUID, factor_id: uuid.UUID, body: TotpConfirm, p: Principal = Depends(need("devices:write")),
                       session: AsyncSession = Depends(get_session)):
    user = await _admin_target(session, p, user_id)
    factor = await session.get(TotpFactor, factor_id)
    if factor is None or factor.user_id != user_id or factor.status != "pending":
        raise HTTPException(404, NOT_FOUND)
    step = totp.verify(box().decrypt(factor.secret_enc, aad=b"totp:" + str(factor.id).encode()), body.code)
    if step is None:
        raise HTTPException(400, {"code": "invalid_otp", "message": "Код не подошёл — проверьте время на телефоне"})
    factor.status, factor.last_step, factor.name = "active", step, body.name
    await authflow.factor_activated(session, user)
    audit(session, "totp.enrolled", actor=p.name, target=str(user_id), ip=p.ip)
    await session.commit()
    return {"id": str(factor.id), "status": factor.status}


@router.delete("/users/{user_id}/totp/{factor_id}", tags=["devices"], status_code=204)
async def totp_delete(user_id: uuid.UUID, factor_id: uuid.UUID, p: Principal = Depends(need("devices:write")),
                      session: AsyncSession = Depends(get_session)):
    await _admin_target(session, p, user_id)
    factor = await session.get(TotpFactor, factor_id)
    if factor is None or factor.user_id != user_id:
        raise HTTPException(404, NOT_FOUND)
    audit(session, "totp.revoked", actor=p.name, target=str(user_id), ip=p.ip, severity="warning")
    await session.delete(factor)
    await session.commit()


# ---------------------------------------------------------------------------------------------------- устройства
def device_out(d: Device) -> dict:
    return {"id": str(d.id), "user_id": str(d.user_id), "name": d.name, "platform": d.platform, "app_version": d.app_version,
            "status": d.status, "push_provider": d.push_provider, "last_seen_at": _dt(d.last_seen_at), "created_at": _dt(d.created_at)}


@router.get("/devices", tags=["devices"])
async def list_devices(page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200), _: Principal = Depends(need("devices:read")),
                       session: AsyncSession = Depends(get_session)):
    stmt = select(Device, User.login).join(User, User.id == Device.user_id).order_by(Device.created_at.desc())
    total = await session.scalar(select(func.count()).select_from(Device))
    rows = (await session.execute(stmt.offset((page - 1) * size).limit(size))).all()
    return {"items": [{**device_out(d), "login": login} for d, login in rows], "total": total}


@router.post("/devices/{device_id}/revoke", tags=["devices"])
async def revoke_device(device_id: uuid.UUID, p: Principal = Depends(need("devices:write")), session: AsyncSession = Depends(get_session)):
    device = _404(await session.get(Device, device_id))
    await _admin_target(session, p, device.user_id)
    device.status, device.push_token_enc = "revoked", None
    audit(session, "device.revoked", actor=p.name, target=str(device.id), ip=p.ip, severity="warning")
    await session.commit()
    return device_out(device)


# -------------------------------------------------------------------------------------------------------- группы
class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field("", max_length=2000)
    directory_group: str | None = Field(None, max_length=1024)


def group_out(g: Group, members: int = 0) -> dict:
    return {"id": str(g.id), "name": g.name, "description": g.description, "directory_group": g.directory_group, "members": members}


@router.get("/groups", tags=["groups"])
async def list_groups(_: Principal = Depends(need("groups:read")), session: AsyncSession = Depends(get_session)):
    from ..models import UserGroup  # noqa: PLC0415
    counts = dict((await session.execute(select(UserGroup.group_id, func.count()).group_by(UserGroup.group_id))).all())
    rows = (await session.scalars(select(Group).order_by(Group.name))).all()
    return {"items": [group_out(g, counts.get(g.id, 0)) for g in rows], "total": len(rows)}


@router.post("/groups", tags=["groups"], status_code=201)
async def create_group(body: GroupIn, p: Principal = Depends(need("groups:write")), session: AsyncSession = Depends(get_session)):
    if await session.scalar(select(Group.id).where(Group.name == body.name)):
        raise HTTPException(409, {"code": "exists", "message": "Группа с таким именем уже есть"})
    group = Group(**body.model_dump())
    session.add(group)
    audit(session, "group.created", actor=p.name, target=body.name, ip=p.ip)
    await session.commit()
    return group_out(group)


@router.put("/groups/{group_id}", tags=["groups"])
async def update_group(group_id: uuid.UUID, body: GroupIn, p: Principal = Depends(need("groups:write")),
                       session: AsyncSession = Depends(get_session)):
    group = _404(await session.get(Group, group_id))
    for k, v in body.model_dump().items():
        setattr(group, k, v)
    audit(session, "group.updated", actor=p.name, target=group.name, ip=p.ip)
    await session.commit()
    return group_out(group)


@router.delete("/groups/{group_id}", tags=["groups"], status_code=204)
async def delete_group(group_id: uuid.UUID, p: Principal = Depends(need("groups:write")), session: AsyncSession = Depends(get_session)):
    group = _404(await session.get(Group, group_id))
    audit(session, "group.deleted", actor=p.name, target=group.name, ip=p.ip)
    await session.delete(group)
    await session.commit()


# ---------------------------------------------------------------------------------------------------- ресурсы
class ResourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    kind: Literal["vpn", "app", "other"] = "vpn"
    description: str = Field("", max_length=2000)
    enabled: bool = True


def resource_out(r: Resource) -> dict:
    return {"id": str(r.id), "name": r.name, "kind": r.kind, "description": r.description, "enabled": r.enabled, "created_at": _dt(r.created_at)}


@router.get("/resources", tags=["resources"])
async def list_resources(_: Principal = Depends(need("resources:read")), session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(Resource).order_by(Resource.name))).all()
    return {"items": [resource_out(r) for r in rows], "total": len(rows)}


@router.post("/resources", tags=["resources"], status_code=201)
async def create_resource(body: ResourceIn, p: Principal = Depends(need("resources:write")), session: AsyncSession = Depends(get_session)):
    if await session.scalar(select(Resource.id).where(Resource.name == body.name)):
        raise HTTPException(409, {"code": "exists", "message": "Ресурс с таким именем уже есть"})
    res = Resource(**body.model_dump())
    session.add(res)
    audit(session, "resource.created", actor=p.name, target=body.name, ip=p.ip)
    await session.commit()
    return resource_out(res)


@router.put("/resources/{resource_id}", tags=["resources"])
async def update_resource(resource_id: uuid.UUID, body: ResourceIn, p: Principal = Depends(need("resources:write")),
                          session: AsyncSession = Depends(get_session)):
    res = _404(await session.get(Resource, resource_id))
    for k, v in body.model_dump().items():
        setattr(res, k, v)
    audit(session, "resource.updated", actor=p.name, target=res.name, ip=p.ip)
    await session.commit()
    return resource_out(res)


@router.delete("/resources/{resource_id}", tags=["resources"], status_code=204)
async def delete_resource(resource_id: uuid.UUID, p: Principal = Depends(need("resources:write")), session: AsyncSession = Depends(get_session)):
    res = _404(await session.get(Resource, resource_id))
    audit(session, "resource.deleted", actor=p.name, target=res.name, ip=p.ip, severity="warning")
    await session.delete(res)
    await session.commit()


# ---------------------------------------------------------------------------------------------- RADIUS-клиенты
class RadiusClientIn(BaseModel):
    resource_id: uuid.UUID
    name: str = Field(min_length=1, max_length=255)
    address: str = Field(max_length=64)
    secret: str | None = Field(None, min_length=16, max_length=128, description="Пусто — сгенерировать; показывается один раз")
    nas_identifier: str | None = Field(None, max_length=255)
    first_factor: Literal["mfa_gate", "gateway"] = "mfa_gate"
    enabled: bool = True

    @field_validator("address")
    @classmethod
    def _addr(cls, v: str) -> str:
        return _cidr(v)


def radius_out(c: RadiusClient) -> dict:
    return {"id": str(c.id), "resource_id": str(c.resource_id), "name": c.name, "address": c.address, "nas_identifier": c.nas_identifier,
            "first_factor": c.first_factor, "enabled": c.enabled, "last_seen_at": _dt(c.last_seen_at), "created_at": _dt(c.created_at)}


@router.get("/radius-clients", tags=["radius"])
async def list_radius(_: Principal = Depends(need("radius:read")), session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(RadiusClient).order_by(RadiusClient.name))).all()
    return {"items": [radius_out(c) for c in rows], "total": len(rows)}


async def _save_radius(session: AsyncSession, client: RadiusClient, body: RadiusClientIn) -> str | None:
    _404(await session.get(Resource, body.resource_id))
    # один адрес — один клиент: иначе неясно, каким секретом проверять пакет (разные по размеру сети допустимы — выигрывает более узкая)
    if await session.scalar(select(RadiusClient.id).where(RadiusClient.address == body.address, RadiusClient.id != client.id)):
        raise HTTPException(409, {"code": "address_in_use", "message": f"Адрес {body.address} уже назначен другому RADIUS-клиенту"})
    for k in ("resource_id", "name", "address", "nas_identifier", "first_factor", "enabled"):
        setattr(client, k, getattr(body, k))
    if body.secret or not client.secret_enc:
        secret = body.secret or new_token(24)
        client.secret_enc = box().encrypt(secret, aad=b"radius:" + str(client.id).encode())
        return secret
    return None


@router.post("/radius-clients", tags=["radius"], status_code=201)
async def create_radius(body: RadiusClientIn, p: Principal = Depends(need("radius:write")), session: AsyncSession = Depends(get_session)):
    client = RadiusClient(id=uuid.uuid4(), secret_enc="")
    secret = await _save_radius(session, client, body)
    session.add(client)
    audit(session, "radius_client.created", actor=p.name, target=body.name, ip=p.ip, address=client.address, first_factor=client.first_factor)
    await session.commit()
    return {**radius_out(client), "secret": secret}


@router.put("/radius-clients/{client_id}", tags=["radius"])
async def update_radius(client_id: uuid.UUID, body: RadiusClientIn, p: Principal = Depends(need("radius:write")),
                        session: AsyncSession = Depends(get_session)):
    client = _404(await session.get(RadiusClient, client_id))
    secret = await _save_radius(session, client, body)
    audit(session, "radius_client.updated", actor=p.name, target=client.name, ip=p.ip, secret_changed=secret is not None,
          first_factor=client.first_factor)
    await session.commit()
    return {**radius_out(client), "secret": secret}


@router.delete("/radius-clients/{client_id}", tags=["radius"], status_code=204)
async def delete_radius(client_id: uuid.UUID, p: Principal = Depends(need("radius:write")), session: AsyncSession = Depends(get_session)):
    client = _404(await session.get(RadiusClient, client_id))
    audit(session, "radius_client.deleted", actor=p.name, target=client.name, ip=p.ip, severity="warning")
    await session.delete(client)
    await session.commit()


# ------------------------------------------------------------------------------------------------- API-клиенты
SCOPES = sorted({f"{a}:{m}" for a in AREAS if a != "admins" for m in ("read", "write")})


class ApiClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    scopes: list[str] = ["auth:write"]
    resource_id: uuid.UUID | None = None
    allowed_cidrs: list[str] = []
    enabled: bool = True

    @field_validator("scopes")
    @classmethod
    def _scopes(cls, v: list[str]) -> list[str]:
        bad = [s for s in v if s not in SCOPES]
        if bad:
            raise ValueError(f"неизвестные права: {', '.join(bad)}")
        return sorted(set(v))

    @field_validator("allowed_cidrs")
    @classmethod
    def _cidrs(cls, v: list[str]) -> list[str]:
        return [_cidr(c) for c in v if c.strip()]


def api_client_out(c: ApiClient) -> dict:
    return {"id": str(c.id), "name": c.name, "key_id": c.key_id, "scopes": c.scopes, "resource_id": str(c.resource_id) if c.resource_id else None,
            "allowed_cidrs": c.allowed_cidrs, "enabled": c.enabled, "last_used_at": _dt(c.last_used_at), "created_at": _dt(c.created_at)}


@router.get("/api-clients", tags=["api-clients"])
async def list_api_clients(_: Principal = Depends(need("apiclients:read")), session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(ApiClient).order_by(ApiClient.name))).all()
    return {"items": [api_client_out(c) for c in rows], "total": len(rows), "scopes": SCOPES}


@router.post("/api-clients", tags=["api-clients"], status_code=201)
async def create_api_client(body: ApiClientIn, p: Principal = Depends(need("apiclients:write")), session: AsyncSession = Depends(get_session)):
    secret = new_token()
    client = ApiClient(key_id="tf_" + new_token(9), secret_hash=sha256_hex(secret), **body.model_dump())
    session.add(client)
    audit(session, "api_client.created", actor=p.name, target=body.name, ip=p.ip, scopes=body.scopes)
    await session.commit()
    return {**api_client_out(client), "secret": secret}


@router.put("/api-clients/{client_id}", tags=["api-clients"])
async def update_api_client(client_id: uuid.UUID, body: ApiClientIn, p: Principal = Depends(need("apiclients:write")),
                            session: AsyncSession = Depends(get_session)):
    client = _404(await session.get(ApiClient, client_id))
    for k, v in body.model_dump().items():
        setattr(client, k, v)
    audit(session, "api_client.updated", actor=p.name, target=client.name, ip=p.ip, scopes=body.scopes)
    await session.commit()
    return api_client_out(client)


@router.post("/api-clients/{client_id}/rotate", tags=["api-clients"])
async def rotate_api_client(client_id: uuid.UUID, p: Principal = Depends(need("apiclients:write")), session: AsyncSession = Depends(get_session)):
    client = _404(await session.get(ApiClient, client_id))
    secret = new_token()
    client.secret_hash = sha256_hex(secret)
    audit(session, "api_client.rotated", actor=p.name, target=client.name, ip=p.ip, severity="warning")
    await session.commit()
    return {**api_client_out(client), "secret": secret}


@router.delete("/api-clients/{client_id}", tags=["api-clients"], status_code=204)
async def delete_api_client(client_id: uuid.UUID, p: Principal = Depends(need("apiclients:write")), session: AsyncSession = Depends(get_session)):
    client = _404(await session.get(ApiClient, client_id))
    audit(session, "api_client.deleted", actor=p.name, target=client.name, ip=p.ip, severity="warning")
    await session.delete(client)
    await session.commit()


# ----------------------------------------------------------------------------------------------------- политики
class PolicyIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    priority: int = Field(100, ge=0, le=100000)
    enabled: bool = True
    resource_ids: list[uuid.UUID] = []
    groups: list[str] = []
    exclude_groups: list[str] = []
    action: Literal["mfa", "allow", "deny"] = "mfa"
    methods: list[Literal["push", "totp"]] = ["push", "totp"]
    on_unavailable: Literal["deny", "allow"] = "deny"


def policy_out(x: Policy) -> dict:
    return {"id": str(x.id), "name": x.name, "priority": x.priority, "enabled": x.enabled, "resource_ids": [str(r) for r in x.resource_ids],
            "groups": x.groups, "exclude_groups": x.exclude_groups, "action": x.action, "methods": x.methods,
            "on_unavailable": x.on_unavailable, "created_at": _dt(x.created_at)}


def _policy_values(body: PolicyIn) -> dict:
    data = body.model_dump()
    data["resource_ids"] = [str(r) for r in body.resource_ids]
    data["groups"] = [g.strip() for g in body.groups if g.strip()]
    data["exclude_groups"] = [g.strip() for g in body.exclude_groups if g.strip()]
    return data


@router.get("/policies", tags=["policies"])
async def list_policies(_: Principal = Depends(need("policies:read")), session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(Policy).order_by(Policy.priority, Policy.name))).all()
    return {"items": [policy_out(x) for x in rows], "total": len(rows)}


@router.post("/policies", tags=["policies"], status_code=201)
async def create_policy(body: PolicyIn, p: Principal = Depends(need("policies:write")), session: AsyncSession = Depends(get_session)):
    policy = Policy(**_policy_values(body))
    session.add(policy)
    audit(session, "policy.created", actor=p.name, target=body.name, ip=p.ip, action=body.action, on_unavailable=body.on_unavailable,
          severity="warning" if body.action == "allow" or body.on_unavailable == "allow" else "info")
    await session.commit()
    return policy_out(policy)


@router.put("/policies/{policy_id}", tags=["policies"])
async def update_policy(policy_id: uuid.UUID, body: PolicyIn, p: Principal = Depends(need("policies:write")),
                        session: AsyncSession = Depends(get_session)):
    policy = _404(await session.get(Policy, policy_id))
    for k, v in _policy_values(body).items():
        setattr(policy, k, v)
    audit(session, "policy.updated", actor=p.name, target=policy.name, ip=p.ip, action=body.action, on_unavailable=body.on_unavailable,
          severity="warning" if body.action == "allow" or body.on_unavailable == "allow" else "info")
    await session.commit()
    return policy_out(policy)


@router.delete("/policies/{policy_id}", tags=["policies"], status_code=204)
async def delete_policy(policy_id: uuid.UUID, p: Principal = Depends(need("policies:write")), session: AsyncSession = Depends(get_session)):
    policy = _404(await session.get(Policy, policy_id))
    audit(session, "policy.deleted", actor=p.name, target=policy.name, ip=p.ip, severity="warning")
    await session.delete(policy)
    await session.commit()


# ------------------------------------------------------------------------------------------------------- LDAP
class LdapIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    kind: Literal["ad", "openldap"] = "ad"
    hosts: list[str] = Field(min_length=1)
    port: int = Field(636, ge=1, le=65535)
    encryption: Literal["ldaps", "starttls", "none"] = "ldaps"
    verify_tls: bool = True
    ca_cert: str | None = Field(None, max_length=65536)
    bind_dn: str = Field("", max_length=1024)
    bind_password: str | None = Field(None, max_length=1024, description="Пусто при изменении — оставить прежний")
    base_dn: str = Field(min_length=1, max_length=1024)
    user_filter: str = Field("(&(objectClass=user)(objectCategory=person))", max_length=1024)
    login_attribute: str = Field("sAMAccountName", max_length=64)
    id_attribute: str = Field("objectGUID", max_length=64)
    name_attribute: str = Field("displayName", max_length=64)
    email_attribute: str = Field("mail", max_length=64)
    phone_attribute: str = Field("mobile", max_length=64)
    nested_groups: bool = True
    allowed_groups: list[str] = []
    timeout: int = Field(5, ge=1, le=60)
    priority: int = Field(10, ge=0, le=1000)
    enabled: bool = True

    @field_validator("hosts")
    @classmethod
    def _hosts(cls, v: list[str]) -> list[str]:
        hosts = [h.strip() for h in v if h.strip()]
        if not hosts:
            raise ValueError("нужен хотя бы один контроллер")
        return hosts


def ldap_out(d: LdapDirectory) -> dict:
    data = {c.name: getattr(d, c.name) for c in LdapDirectory.__table__.columns if c.name not in ("bind_password_enc",)}
    data.update(id=str(d.id), created_at=_dt(d.created_at), has_bind_password=bool(d.bind_password_enc))
    return data


async def _save_ldap(directory: LdapDirectory, body: LdapIn) -> None:
    values = body.model_dump(exclude={"bind_password"})
    values["allowed_groups"] = [g.strip() for g in body.allowed_groups if g.strip()]
    for k, v in values.items():
        setattr(directory, k, v)
    if body.bind_password:
        directory.bind_password_enc = box().encrypt(body.bind_password, aad=b"ldap:" + str(directory.id).encode())


@router.get("/ldap", tags=["ldap"])
async def list_ldap(_: Principal = Depends(need("ldap:read")), session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(LdapDirectory).order_by(LdapDirectory.priority, LdapDirectory.name))).all()
    return {"items": [ldap_out(d) for d in rows], "total": len(rows)}


@router.post("/ldap", tags=["ldap"], status_code=201)
async def create_ldap(body: LdapIn, p: Principal = Depends(need("ldap:write")), session: AsyncSession = Depends(get_session)):
    directory = LdapDirectory(id=uuid.uuid4())
    await _save_ldap(directory, body)
    session.add(directory)
    audit(session, "ldap.created", actor=p.name, target=body.name, ip=p.ip, encryption=body.encryption, verify_tls=body.verify_tls,
          severity="warning" if body.encryption == "none" or not body.verify_tls else "info")
    await session.commit()
    return ldap_out(directory)


@router.put("/ldap/{directory_id}", tags=["ldap"])
async def update_ldap(directory_id: uuid.UUID, body: LdapIn, p: Principal = Depends(need("ldap:write")), session: AsyncSession = Depends(get_session)):
    directory = _404(await session.get(LdapDirectory, directory_id))
    await _save_ldap(directory, body)
    audit(session, "ldap.updated", actor=p.name, target=directory.name, ip=p.ip, encryption=body.encryption, verify_tls=body.verify_tls,
          severity="warning" if body.encryption == "none" or not body.verify_tls else "info")
    await session.commit()
    return ldap_out(directory)


@router.delete("/ldap/{directory_id}", tags=["ldap"], status_code=204)
async def delete_ldap(directory_id: uuid.UUID, p: Principal = Depends(need("ldap:write")), session: AsyncSession = Depends(get_session)):
    directory = _404(await session.get(LdapDirectory, directory_id))
    audit(session, "ldap.deleted", actor=p.name, target=directory.name, ip=p.ip, severity="warning")
    await session.delete(directory)
    await session.commit()


class DiagnoseIn(BaseModel):
    login: str | None = Field(None, max_length=255)
    password: str | None = Field(None, max_length=1024)


@router.post("/ldap/{directory_id}/diagnose", tags=["ldap"])
async def diagnose_ldap(directory_id: uuid.UUID, body: DiagnoseIn, p: Principal = Depends(need("ldap:write")),
                        session: AsyncSession = Depends(get_session)):
    from ..services import ldap as ldap_service   # noqa: PLC0415
    directory = _404(await session.get(LdapDirectory, directory_id))
    steps = await ldap_service.diagnose(directory, body.login or None, body.password or None)
    audit(session, "ldap.diagnosed", actor=p.name, target=directory.name, ip=p.ip, login=body.login, ok=all(s.get("ok") for s in steps))
    await session.commit()
    return {"steps": steps, "ok": all(s.get("ok") for s in steps)}


# -------------------------------------------------------------------------------------------- история входов
def request_out(r: AuthRequest) -> dict:
    return {"id": str(r.id), "login": r.login, "resource": r.resource_name, "channel": r.channel, "client": r.client_name,
            "source_ip": r.source_ip, "nas_ip": r.nas_ip, "status": r.status, "method": r.method, "reason": r.reason,
            "reason_text": authflow.REASONS.get(r.reason or "", ""), "created_at": _dt(r.created_at), "decided_at": _dt(r.decided_at),
            "latency_ms": int((r.decided_at - r.created_at).total_seconds() * 1000) if r.decided_at and r.created_at else None}


@router.get("/auth-requests", tags=["requests"])
async def list_requests(login: str = "", status: str = "", channel: str = "", reason: str = "", page: int = Query(1, ge=1),
                        size: int = Query(50, ge=1, le=200), _: Principal = Depends(need("requests:read")),
                        session: AsyncSession = Depends(get_session)):
    stmt = select(AuthRequest).order_by(AuthRequest.created_at.desc())
    if login:
        stmt = stmt.where(AuthRequest.login.like(f"%{login.lower()}%"))
    for col, val in ((AuthRequest.status, status), (AuthRequest.channel, channel), (AuthRequest.reason, reason)):
        if val:
            stmt = stmt.where(col == val)
    rows, total = await _page(session, stmt, page, size)
    return {"items": [request_out(r) for r in rows], "total": total}


@router.get("/auth-requests/problems", tags=["requests"])
async def problems(days: int = Query(7, ge=1, le=90), _: Principal = Depends(need("requests:read")), session: AsyncSession = Depends(get_session)):
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (await session.execute(
        select(AuthRequest.login, AuthRequest.reason, func.count().label("n"), func.max(AuthRequest.created_at).label("last"))
        .where(AuthRequest.created_at >= since, AuthRequest.status.in_(["rejected", "denied", "expired"]))
        .group_by(AuthRequest.login, AuthRequest.reason).order_by(func.count().desc()).limit(200))).all()
    return {"items": [{"login": r.login, "reason": r.reason, "reason_text": authflow.REASONS.get(r.reason or "", ""), "count": r.n,
                       "last": _dt(r.last)} for r in rows]}


# ------------------------------------------------------------------------------------------------------- аудит
def _audit_stmt(event: str, actor: str, severity: str, result: str) -> Select:
    stmt = select(AuditEvent).order_by(AuditEvent.at.desc())
    if event:
        stmt = stmt.where(AuditEvent.event.like(f"{event}%"))
    if actor:
        stmt = stmt.where(AuditEvent.actor.like(f"%{actor.lower()}%"))
    if severity:
        stmt = stmt.where(AuditEvent.severity == severity)
    if result:
        stmt = stmt.where(AuditEvent.result == result)
    return stmt


def audit_out(a: AuditEvent) -> dict:
    return {"id": a.id, "at": _dt(a.at), "event": a.event, "severity": a.severity, "actor": a.actor, "target": a.target, "ip": a.ip,
            "result": a.result, "details": a.details}


@router.get("/audit", tags=["audit"])
async def list_audit(event: str = "", actor: str = "", severity: str = "", result: str = "", page: int = Query(1, ge=1),
                     size: int = Query(50, ge=1, le=200), _: Principal = Depends(need("audit:read")), session: AsyncSession = Depends(get_session)):
    rows, total = await _page(session, _audit_stmt(event, actor, severity, result), page, size)
    return {"items": [audit_out(a) for a in rows], "total": total}


@router.get("/audit/export.csv", tags=["audit"])
async def export_audit(event: str = "", actor: str = "", severity: str = "", result: str = "", _: Principal = Depends(need("audit:read")),
                       session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(_audit_stmt(event, actor, severity, result).limit(100000))).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["at", "event", "severity", "actor", "target", "ip", "result", "details"])
    for a in rows:   # защита от CSV-инъекции: значения, начинающиеся с формульных символов, экранируются
        writer.writerow([("'" + v if isinstance(v, str) and v[:1] in "=+-@" else v)
                         for v in (_dt(a.at), a.event, a.severity, a.actor, a.target, a.ip, a.result, str(a.details))])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=mfa-gate-audit.csv"})


# ------------------------------------------------------------------------------------------------- состояние
@router.get("/dashboard", tags=["system"])
async def dashboard(_: Principal = Depends(need("system:read")), session: AsyncSession = Depends(get_session)):
    since = datetime.now(UTC) - timedelta(hours=24)
    by_status = dict((await session.execute(select(AuthRequest.status, func.count()).where(AuthRequest.created_at >= since)
                                            .group_by(AuthRequest.status))).all())
    count = lambda model, *where: session.scalar(select(func.count()).select_from(model).where(*where))   # noqa: E731
    return {
        "users": await count(User), "devices": await count(Device, Device.status == "active"),
        "totp": await count(TotpFactor, TotpFactor.status == "active"), "resources": await count(Resource),
        "radius_clients": await count(RadiusClient, RadiusClient.enabled), "locked_users": await count(User, User.locked_until > datetime.now(UTC)),
        "users_without_factor": await session.scalar(select(func.count()).select_from(User).where(
            ~select(Device.id).where(Device.user_id == User.id, Device.status == "active").exists(),
            ~select(TotpFactor.id).where(TotpFactor.user_id == User.id, TotpFactor.status == "active").exists())),
        "last24h": by_status,
        "recent": [request_out(r) for r in (await session.scalars(select(AuthRequest).order_by(AuthRequest.created_at.desc()).limit(8))).all()],
        "attention": [audit_out(a) for a in (await session.scalars(select(AuditEvent).where(AuditEvent.severity.in_(["warning", "critical"]))
                                                                    .order_by(AuditEvent.at.desc()).limit(8))).all()],
    }


@router.get("/system/diagnostics", tags=["system"])
async def diagnostics(_: Principal = Depends(need("system:read")), session: AsyncSession = Depends(get_session)):
    from ..services.health import checks   # noqa: PLC0415
    return await checks(session)


@router.get("/system/publication", tags=["system"])
async def publication(_: Principal = Depends(need("system:read")), session: AsyncSession = Depends(get_session)):
    from ..services import publication as pub   # noqa: PLC0415
    from ..services.push import build_providers   # noqa: PLC0415
    clients = (await session.scalars(select(RadiusClient).where(RadiusClient.enabled).order_by(RadiusClient.name))).all()
    directories = (await session.scalars(select(LdapDirectory).where(LdapDirectory.enabled).order_by(LdapDirectory.priority))).all()
    return pub.build(settings(), [(c.name, c.address, c.first_factor) for c in clients],
                     [(d.name, list(d.hosts), d.port, d.encryption) for d in directories], list(build_providers()))
