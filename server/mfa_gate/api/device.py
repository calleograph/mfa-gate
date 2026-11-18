"""Device API v1 — контракт описан в docs/protocol/device-api.md."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session, sessionmaker
from ..models import AuthRequest, Device, OneTimeToken, User
from ..security.crypto import box, load_device_key, new_token, sha256_hex
from ..services import authflow, events
from ..services.audit import audit
from ..services.push import build_providers
from .deps import client_ip, device_auth, enroll_limiter

router = APIRouter(prefix="/api/device/v1", tags=["device"])
PUSH_PROVIDERS = ("fcm", "hms", "unifiedpush", "none")


class PushChannel(BaseModel):
    provider: str = Field("none", pattern="^(fcm|hms|unifiedpush|none)$")
    token: str | None = Field(None, max_length=4096)


class EnrollIn(BaseModel):
    token: str = Field(max_length=128)
    device_name: str = Field(min_length=1, max_length=128)
    platform: str = Field("android", max_length=32)
    app_version: str = Field("", max_length=32)
    public_key: str = Field(max_length=512)
    push: PushChannel = PushChannel()


class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(approve|deny)$")
    signature: str = Field(max_length=256)


def push_config() -> dict:
    fcm = build_providers().get("fcm")
    return {"fcm": fcm.client_config() if fcm else None}


def _set_push(device: Device, push: PushChannel) -> None:
    device.push_provider = push.provider if push.token else "none"
    device.push_token_enc = box().encrypt(push.token, aad=b"push:" + str(device.id).encode()) if push.token else None


@router.post("/enroll")
async def enroll(body: EnrollIn, request: Request, session: AsyncSession = Depends(get_session)):
    await enroll_limiter.check(session, client_ip(request))
    try:
        load_device_key(body.public_key)
    except ValueError:
        raise HTTPException(400, {"code": "invalid_public_key", "message": "Нужен открытый ключ EC P-256"}) from None
    now = datetime.now(UTC)
    # одноразовость: токен гасится условным UPDATE, второй параллельный запрос получит 410
    token_id = await session.scalar(update(OneTimeToken)
                                    .where(OneTimeToken.token_hash == sha256_hex(body.token), OneTimeToken.purpose == "enroll_device",
                                           OneTimeToken.used_at.is_(None), OneTimeToken.expires_at > now)
                                    .values(used_at=now).returning(OneTimeToken.user_id))
    user = await session.get(User, token_id) if token_id else None
    if user is None or user.status != "active":
        await session.rollback()
        raise HTTPException(410, {"code": "token_expired_or_used", "message": "Ссылка привязки недействительна или уже использована"})
    secret = new_token()
    device = Device(id=uuid.uuid4(), user_id=user.id, name=body.device_name, platform=body.platform, app_version=body.app_version,
                    public_key=body.public_key, secret_hash=sha256_hex(secret), last_seen_at=now)
    _set_push(device, body.push)
    session.add(device)
    audit(session, "device.enrolled", actor=user.login, target=str(device.id), ip=client_ip(request), device_name=body.device_name,
          push=device.push_provider)
    await session.commit()
    cfg = settings()
    return {"device_id": str(device.id), "device_secret": secret, "user": {"login": user.login, "display_name": user.display_name},
            "server": {"name": cfg.server_name, "url": cfg.public_url}, "poll_interval": 25, "push_config": push_config()}


def _item(req: AuthRequest, with_challenge: bool) -> dict:
    return {"id": str(req.id), "login": req.login, "resource": req.resource_name, "source_ip": req.source_ip, "location": None,
            "client": req.client_name, "created_at": req.created_at.isoformat(), "expires_at": req.expires_at.isoformat() if req.expires_at else None,
            "challenge": req.challenge if with_challenge and req.status == "pending" else None, "status": req.status}


async def _pending(device: Device) -> list[dict]:
    async with sessionmaker()() as session:
        rows = (await session.scalars(select(AuthRequest).where(
            AuthRequest.user_id == device.user_id, AuthRequest.status == "pending", AuthRequest.method == "push",
            AuthRequest.expires_at > datetime.now(UTC)).order_by(AuthRequest.created_at))).all()
    return [_item(r, True) for r in rows]


@router.get("/requests")
async def requests(status: str | None = Query(None, pattern="^pending$"), wait: int = Query(0, ge=0, le=30),
                   limit: int = Query(50, ge=1, le=200), device: Device = Depends(device_auth),
                   session: AsyncSession = Depends(get_session)):
    if status == "pending":
        items = await _pending(device)
        if not items and wait:
            await events.hub.wait(f"user:{device.user_id}", wait)
            items = await _pending(device)
        return {"items": items, "server_time": datetime.now(UTC).isoformat()}
    rows = (await session.scalars(select(AuthRequest).where(AuthRequest.user_id == device.user_id, AuthRequest.method == "push")
                                  .order_by(AuthRequest.created_at.desc()).limit(limit))).all()
    return {"items": [_item(r, False) for r in rows], "server_time": datetime.now(UTC).isoformat()}


@router.get("/requests/{request_id}")
async def request_one(request_id: uuid.UUID, device: Device = Depends(device_auth), session: AsyncSession = Depends(get_session)):
    req = await session.get(AuthRequest, request_id)
    if req is None or req.user_id != device.user_id or req.method != "push":
        raise HTTPException(404, {"code": "not_found", "message": "Запрос не найден"})
    return {**_item(req, True), "server_time": datetime.now(UTC).isoformat()}


@router.post("/requests/{request_id}/decision")
async def decision(request_id: uuid.UUID, body: DecisionIn, request: Request, device: Device = Depends(device_auth),
                   session: AsyncSession = Depends(get_session)):
    result = await authflow.decide(session, request_id, device, body.decision, body.signature)
    await session.commit()
    errors = {"not_found": (404, "Запрос не найден"), "bad_signature": (400, "Подпись не прошла проверку"),
              "already_decided": (409, "По запросу уже принято решение"), "expired": (410, "Время на ответ истекло")}
    if result in errors:
        code, message = errors[result]
        raise HTTPException(code, {"code": result, "message": message})
    return {"status": result}


@router.put("/push")
async def set_push(body: PushChannel, device: Device = Depends(device_auth), session: AsyncSession = Depends(get_session)):
    _set_push(device, body)
    await session.commit()
    return {"provider": device.push_provider}


@router.get("/config")
async def config(_: Device = Depends(device_auth)):
    return {"push_config": push_config(), "poll_interval": 25}


@router.get("/ping")
async def ping(device: Device = Depends(device_auth)):
    return {"status": device.status}


@router.delete("/self")
async def unbind(request: Request, device: Device = Depends(device_auth), session: AsyncSession = Depends(get_session)):
    device.status, device.push_token_enc = "revoked", None
    owner = await session.get(User, device.user_id)
    audit(session, "device.unbound", actor=owner.login if owner else "?", target=str(device.id), ip=client_ip(request))
    await session.commit()
    return {"status": "revoked"}
