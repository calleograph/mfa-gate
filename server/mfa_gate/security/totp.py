"""Одноразовые коды второго фактора (TOTP, RFC 6238): HMAC-SHA1, 6 цифр, шаг 30 с — совместимо с Google/Microsoft Authenticator, FreeOTP, Aegis.

Только стандартная библиотека, кроме QR-картинки (segno, чистый Python). Секрет пользователя хранится зашифрованным (ключ MFA_GATE_MASTER_KEY, AAD привязан к
пользователю), резервные коды — только в виде хэша. Повтор уже принятого кода отклоняется: сохраняется номер последнего принятого шага.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import secrets
import struct
import time
from urllib.parse import quote

STEP_S = 30
DIGITS = 6
WINDOW = 1                       # допускаются соседние шаги (расхождение часов до ±30 с)


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    return str((struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 10**DIGITS).zfill(DIGITS)


def code_at(secret: str, at: float | None = None) -> str:
    return _hotp(secret, int((time.time() if at is None else at) // STEP_S))


def verify(secret: str, code: str, last_step: int | None = None, at: float | None = None) -> int | None:
    """Номер принятого шага или None. Шаг не больше `last_step` (уже использованный) не принимается — защита от повтора перехваченного кода."""
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != DIGITS:
        return None
    now = int((time.time() if at is None else at) // STEP_S)
    hit: int | None = None
    for step in range(now - WINDOW, now + WINDOW + 1):          # без раннего выхода: время проверки не зависит от совпадения
        if hmac.compare_digest(_hotp(secret, step), code) and (last_step is None or step > last_step):
            hit = step
    return hit


def otpauth_uri(issuer: str, account: str, secret: str) -> str:
    label = quote(f"{issuer}:{account}", safe="")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer, safe='')}&algorithm=SHA1&digits={DIGITS}&period={STEP_S}"


def qr_svg(data: str) -> str:
    import segno  # noqa: PLC0415

    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="svg", scale=5, border=2, xmldecl=False, svgns=True, nl=False, omitsize=True)
    return buf.getvalue().decode("utf-8")


# ----------------------------------------------------------------------------------------------- резервные коды
def hash_backup(master_key_b64: str, user_id: str, code: str) -> str:
    """HMAC-SHA256 нормализованного кода на ключе, производном от мастер-ключа, с привязкой к пользователю: украденная база без
    master.key не позволяет перебирать ~50 бит кода офлайн."""
    pepper = hmac.new(base64.b64decode(master_key_b64), b"mfa-gate-backup-code-v1", hashlib.sha256).digest()
    return hmac.new(pepper, f"{user_id}:{code}".encode(), hashlib.sha256).hexdigest()
