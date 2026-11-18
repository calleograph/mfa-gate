"""Кодек RADIUS (RFC 2865 — Access-Request/Accept/Reject/Challenge, RFC 3579 — Message-Authenticator). Только стандартная библиотека."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import struct
from dataclasses import dataclass, field

ACCESS_REQUEST, ACCESS_ACCEPT, ACCESS_REJECT, ACCESS_CHALLENGE = 1, 2, 3, 11

USER_NAME, USER_PASSWORD, CHAP_PASSWORD, NAS_IP_ADDRESS = 1, 2, 3, 4
REPLY_MESSAGE, STATE, CLASS, SESSION_TIMEOUT = 18, 24, 25, 27
CALLING_STATION_ID, NAS_IDENTIFIER, MESSAGE_AUTHENTICATOR = 31, 32, 80

MAX_PACKET = 4096


class PacketError(ValueError):
    pass


@dataclass
class Packet:
    code: int
    identifier: int
    authenticator: bytes
    attributes: list[tuple[int, bytes]] = field(default_factory=list)

    def get(self, attr: int) -> bytes | None:
        return next((v for t, v in self.attributes if t == attr), None)

    def text(self, attr: int) -> str | None:
        value = self.get(attr)
        return value.decode("utf-8", "replace") if value is not None else None


def decode(data: bytes) -> Packet:
    if len(data) < 20 or len(data) > MAX_PACKET:
        raise PacketError("длина пакета вне допустимого диапазона")
    code, identifier, length = struct.unpack("!BBH", data[:4])
    if length < 20 or length > len(data):
        raise PacketError("поле Length не совпадает с размером")
    attrs, pos = [], 20
    while pos < length:
        if pos + 2 > length:
            raise PacketError("обрезанный атрибут")
        atype, alen = data[pos], data[pos + 1]
        if alen < 2 or pos + alen > length:
            raise PacketError("неверная длина атрибута")
        attrs.append((atype, data[pos + 2:pos + alen]))
        pos += alen
    return Packet(code, identifier, data[4:20], attrs)


def _encode_attrs(attributes: list[tuple[int, bytes]]) -> bytes:
    out = b""
    for atype, value in attributes:
        if len(value) > 253:
            raise PacketError(f"атрибут {atype} длиннее 253 байт")
        out += struct.pack("!BB", atype, len(value) + 2) + value
    return out


def _md5(*parts: bytes) -> bytes:
    return hashlib.md5(b"".join(parts), usedforsecurity=False).digest()


def decrypt_password(value: bytes, secret: bytes, authenticator: bytes) -> str:
    if not value or len(value) % 16 or len(value) > 128:
        raise PacketError("неверная длина User-Password")
    out, prev = b"", authenticator
    for i in range(0, len(value), 16):
        block = value[i:i + 16]
        out += bytes(a ^ b for a, b in zip(block, _md5(secret, prev), strict=True))
        prev = block
    return out.rstrip(b"\x00").decode("utf-8", "replace")


def encrypt_password(password: str, secret: bytes, authenticator: bytes) -> bytes:
    raw = password.encode()
    raw += b"\x00" * (-len(raw) % 16 or (16 if not raw else 0))
    out, prev = b"", authenticator
    for i in range(0, len(raw), 16):
        block = bytes(a ^ b for a, b in zip(raw[i:i + 16], _md5(secret, prev), strict=True))
        out += block
        prev = block
    return out


def _with_zeroed_ma(data: bytes) -> bytes | None:
    """Копия пакета с обнулённым значением Message-Authenticator (для HMAC) или None, если атрибута нет."""
    pos, length = 20, struct.unpack("!H", data[2:4])[0]
    while pos < length:
        atype, alen = data[pos], data[pos + 1]
        if atype == MESSAGE_AUTHENTICATOR:
            if alen != 18:
                return None
            return data[:pos + 2] + b"\x00" * 16 + data[pos + 18:]
        pos += alen
    return None


def verify_message_authenticator(data: bytes, packet: Packet, secret: bytes) -> bool | None:
    """True/False — атрибут есть и верен/неверен, None — атрибута нет."""
    value = packet.get(MESSAGE_AUTHENTICATOR)
    if value is None:
        return None
    zeroed = _with_zeroed_ma(data)
    if zeroed is None:
        return False
    return hmac.compare_digest(hmac.new(secret, zeroed[:struct.unpack("!H", data[2:4])[0]], hashlib.md5).digest(), value)


def encode_response(code: int, request: Packet, secret: bytes, attributes: list[tuple[int, bytes]]) -> bytes:
    """Ответ с Message-Authenticator первым атрибутом (RFC 3579 + рекомендация против BlastRADIUS) и Response Authenticator."""
    attrs = _encode_attrs([(MESSAGE_AUTHENTICATOR, b"\x00" * 16), *attributes])
    length = 20 + len(attrs)
    header = struct.pack("!BBH", code, request.identifier, length)
    ma = hmac.new(secret, header + request.authenticator + attrs, hashlib.md5).digest()
    attrs = attrs[:2] + ma + attrs[18:]
    return header + _md5(header, request.authenticator, attrs, secret) + attrs


def encode_request(identifier: int, secret: bytes, attributes: list[tuple[int, bytes]], authenticator: bytes | None = None,
                   message_authenticator: bool = True) -> bytes:
    """Access-Request (для тестов, диагностики и CLI-проверки). User-Password передаётся открытым и шифруется здесь."""
    authenticator = authenticator or secrets.token_bytes(16)
    attrs = [(t, encrypt_password(v.decode(), secret, authenticator) if t == USER_PASSWORD else v) for t, v in attributes]
    if message_authenticator:
        attrs = [(MESSAGE_AUTHENTICATOR, b"\x00" * 16), *attrs]
    body = _encode_attrs(attrs)
    data = struct.pack("!BBH", ACCESS_REQUEST, identifier, 20 + len(body)) + authenticator + body
    if message_authenticator:
        ma = hmac.new(secret, data, hashlib.md5).digest()
        data = data[:22] + ma + data[38:]
    return data


def decode_ip(value: bytes | None) -> str | None:
    return ".".join(str(b) for b in value) if value is not None and len(value) == 4 else None


def verify_response(data: bytes, request_authenticator: bytes, secret: bytes) -> bool:
    expected = _md5(data[:4], request_authenticator, data[20:], secret)
    return hmac.compare_digest(expected, data[4:20])
