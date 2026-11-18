"""Точка входа контейнера и инструменты администратора: python -m mfa_gate <команда>."""
from __future__ import annotations

import argparse
import asyncio
import base64
import getpass
import json
import logging
import os
import secrets
import socket
import sys
import time
from datetime import UTC, datetime, timedelta

from .config import settings


def _logging() -> None:
    logging.basicConfig(level=settings().log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def _wait_db(timeout: float = 60) -> None:
    from sqlalchemy import text   # noqa: PLC0415

    from .db import engine   # noqa: PLC0415
    deadline = time.monotonic() + timeout
    while True:
        try:
            async with engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
            return
        except Exception as exc:
            if time.monotonic() > deadline:
                raise SystemExit(f"база данных недоступна: {type(exc).__name__}") from exc
            await asyncio.sleep(2)


def cmd_migrate(_args) -> None:
    from pathlib import Path   # noqa: PLC0415

    from alembic import command   # noqa: PLC0415
    from alembic.config import Config   # noqa: PLC0415
    asyncio.run(_wait_db())
    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    command.upgrade(cfg, "head")
    print("миграции применены")


def cmd_api(_args) -> None:
    import uvicorn   # noqa: PLC0415
    uvicorn.run("mfa_gate.api.app:app", host=os.environ.get("MFA_GATE_HTTP_HOST", "0.0.0.0"), port=int(os.environ.get("MFA_GATE_HTTP_PORT", "8080")), proxy_headers=False,  # noqa: S104 — в контейнере, наружу публикует compose
                log_level=settings().log_level.lower(), access_log=False)


def cmd_radius(_args) -> None:
    from .radius.server import serve   # noqa: PLC0415
    _logging()
    asyncio.run(serve())


def cmd_worker(_args) -> None:
    from .worker import run   # noqa: PLC0415
    _logging()
    asyncio.run(run())


async def _create_admin(login: str, password: str, display_name: str, temporary: bool = False) -> None:
    from sqlalchemy import select   # noqa: PLC0415

    from .db import sessionmaker   # noqa: PLC0415
    from .models import User   # noqa: PLC0415
    from .security.crypto import hash_password   # noqa: PLC0415
    from .services.audit import audit   # noqa: PLC0415
    async with sessionmaker()() as session:
        user = await session.scalar(select(User).where(User.login == login))
        if user is None:
            user = User(login=login, source="local", display_name=display_name, directory_groups=[])
            session.add(user)
        if user.source != "local":
            raise SystemExit("пользователь из каталога: назначьте роль в панели, пароль меняется в каталоге")
        user.password_hash, user.admin_role, user.status = hash_password(password), "superadmin", "active"
        user.must_change_password, user.failed_attempts, user.locked_until = temporary, 0, None
        audit(session, "admin.created_cli", target=login, severity="warning")
        await session.commit()


async def _has_superadmin() -> bool:
    from sqlalchemy import select   # noqa: PLC0415

    from .db import sessionmaker   # noqa: PLC0415
    from .models import User   # noqa: PLC0415
    async with sessionmaker()() as session:
        return bool(await session.scalar(select(User.id).where(User.admin_role == "superadmin", User.status == "active").limit(1)))


def cmd_create_admin(args) -> None:
    password = args.password_stdin and sys.stdin.readline().strip()
    generated = not password
    password = password or secrets.token_urlsafe(18)
    if len(password) < 12:
        raise SystemExit("пароль короче 12 символов")
    async def flow() -> bool:   # одним asyncio.run: пул соединений привязан к циклу событий
        if args.if_no_admin and await _has_superadmin():
            return False
        await _create_admin(args.login.strip().lower(), password, args.name or args.login, temporary=args.temporary)
        return True
    if not asyncio.run(flow()):
        print("главный администратор уже есть — пропущено")
        return
    print(f"главный администратор {args.login} создан")
    if generated:
        print(f"пароль (показывается один раз): {password}")
    if args.temporary:
        print("пароль временный: при первом входе в панель его нужно сменить")
    print("при первом входе в панель будет предложено подключить TOTP")


async def _emergency(login: str, minutes: int) -> str:
    from sqlalchemy import select   # noqa: PLC0415

    from .db import sessionmaker   # noqa: PLC0415
    from .models import OneTimeToken, User   # noqa: PLC0415
    from .security.crypto import sha256_hex   # noqa: PLC0415
    from .services.audit import audit   # noqa: PLC0415
    code = "-".join(secrets.token_hex(3) for _ in range(3))
    async with sessionmaker()() as session:
        user = await session.scalar(select(User).where(User.login == login))
        if user is None or not user.admin_role:
            raise SystemExit("администратор не найден")
        session.add(OneTimeToken(user_id=user.id, purpose="emergency_admin", token_hash=sha256_hex(code),
                                 expires_at=datetime.now(UTC) + timedelta(minutes=minutes), created_by="cli"))
        audit(session, "admin.emergency_code_issued", target=login, severity="critical", minutes=minutes)
        await session.commit()
    return code


def cmd_emergency(args) -> None:
    code = asyncio.run(_emergency(args.login.strip().lower(), args.minutes))
    print(f"одноразовый аварийный код для {args.login} (действует {args.minutes} мин, заменяет второй фактор один раз):")
    print(code)


def cmd_radius_test(args) -> None:
    """Отправляет Access-Request и печатает ответ — для диагностики и сквозной проверки."""
    from .radius import packet as rp   # noqa: PLC0415
    secret = (args.secret or getpass.getpass("shared secret: ")).encode()
    password = args.password if args.password is not None else getpass.getpass("пароль: ")
    attrs = [(rp.USER_NAME, args.user.encode()), (rp.USER_PASSWORD, password.encode()), (rp.NAS_IDENTIFIER, b"mfa-gate-cli")]
    if args.calling_station:
        attrs.append((rp.CALLING_STATION_ID, args.calling_station.encode()))
    data = rp.encode_request(secrets.randbelow(256), secret, attrs)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(args.timeout)
    started = time.monotonic()
    sock.sendto(data, (args.host, args.port))
    try:
        reply, _ = sock.recvfrom(4096)
    except TimeoutError:
        print(json.dumps({"result": "timeout", "seconds": args.timeout}))
        raise SystemExit(2) from None
    pkt = rp.decode(reply)
    names = {rp.ACCESS_ACCEPT: "accept", rp.ACCESS_REJECT: "reject", rp.ACCESS_CHALLENGE: "challenge"}
    print(json.dumps({"result": names.get(pkt.code, pkt.code), "authenticator_ok": rp.verify_response(reply, data[4:20], secret),
                      "message": pkt.text(rp.REPLY_MESSAGE), "seconds": round(time.monotonic() - started, 2)}, ensure_ascii=False))
    raise SystemExit(0 if pkt.code == rp.ACCESS_ACCEPT else 1)


def cmd_diagnose(_args) -> None:
    from .db import sessionmaker   # noqa: PLC0415
    from .services.health import checks   # noqa: PLC0415

    async def run():
        async with sessionmaker()() as session:
            return await checks(session)
    result = asyncio.run(run())
    for c in result["checks"]:
        mark = {"ok": "OK  ", "warning": "WARN", "error": "FAIL"}[c["status"]]
        print(f"{mark} {c['name']}: {c['message']}" + (f" → {c['action']}" if c["action"] else ""))
    raise SystemExit(0 if result["status"] != "error" else 1)


async def _api_client(name: str, scopes: list[str]) -> tuple[str, str]:
    from .api.manage import SCOPES   # noqa: PLC0415
    from .db import sessionmaker   # noqa: PLC0415
    from .models import ApiClient   # noqa: PLC0415
    from .security.crypto import new_token, sha256_hex   # noqa: PLC0415
    from .services.audit import audit   # noqa: PLC0415
    scopes = SCOPES if scopes == ["all"] else scopes
    if bad := [s for s in scopes if s not in SCOPES]:
        raise SystemExit(f"неизвестные права: {', '.join(bad)}; доступны: {', '.join(SCOPES)}")
    secret, key_id = new_token(), "tf_" + new_token(9)
    async with sessionmaker()() as session:
        session.add(ApiClient(name=name, key_id=key_id, secret_hash=sha256_hex(secret), scopes=sorted(scopes), allowed_cidrs=[]))
        audit(session, "api_client.created", actor="cli", target=name, scopes=scopes, severity="warning")
        await session.commit()
    return key_id, secret


def cmd_api_client(args) -> None:
    key_id, secret = asyncio.run(_api_client(args.name, args.scopes))
    print(f"{key_id}:{secret}")


def cmd_gen_key(_args) -> None:
    print(base64.b64encode(secrets.token_bytes(32)).decode())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mfa_gate")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, fn in (("api", cmd_api), ("radius", cmd_radius), ("worker", cmd_worker), ("migrate", cmd_migrate),
                     ("diagnose", cmd_diagnose), ("gen-master-key", cmd_gen_key)):
        sub.add_parser(name).set_defaults(fn=fn)
    p = sub.add_parser("create-admin", help="создать/восстановить главного администратора (локального)")
    p.add_argument("login")
    p.add_argument("--name", default="")
    p.add_argument("--password-stdin", action="store_true", help="прочитать пароль из stdin; без флага пароль генерируется")
    p.add_argument("--temporary", action="store_true", help="пароль временный: смена обязательна при первом входе")
    p.add_argument("--if-no-admin", action="store_true", help="ничего не делать, если активный главный администратор уже есть")
    p.set_defaults(fn=cmd_create_admin)
    p = sub.add_parser("emergency-access", help="одноразовый код вместо второго фактора для входа администратора")
    p.add_argument("login")
    p.add_argument("--minutes", type=int, default=15, choices=range(1, 61), metavar="1..60")
    p.set_defaults(fn=cmd_emergency)
    p = sub.add_parser("create-api-client", help="выпустить ключ API (печатает key_id:secret один раз)")
    p.add_argument("name")
    p.add_argument("--scopes", nargs="+", default=["auth:write"], help="права или all")
    p.set_defaults(fn=cmd_api_client)
    p = sub.add_parser("radius-test", help="отправить тестовый Access-Request")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=1812)
    p.add_argument("--secret")
    p.add_argument("--user", required=True)
    p.add_argument("--password")
    p.add_argument("--calling-station")
    p.add_argument("--timeout", type=float, default=60)
    p.set_defaults(fn=cmd_radius_test)
    args = parser.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
