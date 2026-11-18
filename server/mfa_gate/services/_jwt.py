"""Минимальный JWT RS256 для OAuth service account (одна функция — не повод тянуть библиотеку)."""
from __future__ import annotations

import json
from collections.abc import Callable

from ..security.crypto import b64url


def encode_rs256(claims: dict, sign: Callable[[bytes], bytes]) -> str:
    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}".encode()
    return f"{header}.{body}.{b64url(sign(signing_input))}"
