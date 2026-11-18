"""Токены, хэши, пароли и проверка подписи устройства."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_der_public_key

from ..config import settings
from .secretbox import SecretBox

_ph = PasswordHasher()   # argon2id с параметрами библиотеки по умолчанию (RFC 9106, low-memory)
DECISION_PREFIX = "mfa-gate-decision-v1"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def new_token(nbytes: int = 32) -> str:
    return b64url(secrets.token_bytes(nbytes))


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def same_hash(text: str, expected_hex: str) -> bool:
    return hmac.compare_digest(sha256_hex(text), expected_hex)


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        _ph.hash(password)   # выравнивание времени ответа для несуществующих учётных записей
        return False
    try:
        return _ph.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def load_device_key(public_key_b64url: str) -> ec.EllipticCurvePublicKey:
    """Открытый ключ устройства: только EC P-256 (SPKI DER)."""
    key = load_der_public_key(b64url_decode(public_key_b64url))
    if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
        raise ValueError("ожидается ключ EC P-256")
    return key


def decision_message(request_id: str, device_id: str, challenge: str, decision: str) -> bytes:
    # все поля из ограниченных алфавитов (uuid, base64url, перечисление), поэтому разделитель \n однозначен
    return "\n".join([DECISION_PREFIX, request_id, device_id, challenge, decision]).encode()


def verify_decision(public_key_b64url: str, message: bytes, signature_b64url: str) -> bool:
    try:
        load_device_key(public_key_b64url).verify(b64url_decode(signature_b64url), message, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError):
        return False


@lru_cache
def box() -> SecretBox:
    key = settings().master_key
    if not key:
        raise RuntimeError("MFA_GATE_MASTER_KEY не задан: без мастер-ключа секреты нельзя ни сохранить, ни прочитать")
    return SecretBox(key)
