"""Доставка push-сигнала «есть запрос». В уведомлении только id запроса: данные устройство забирает само по HTTPS.

Провайдер выбирается по устройству (его push_provider). none — устройство получает запросы long-poll'ом, отправка не нужна.
"""
from __future__ import annotations

import json
import logging
import time
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from ..config import settings
from . import _jwt

log = logging.getLogger(__name__)


class PushError(Exception):
    def __init__(self, message: str, *, invalid_token: bool = False):
        super().__init__(message)
        self.invalid_token = invalid_token


def payload(request_id: str, device_id: str) -> dict[str, str]:
    return {"type": "auth_request", "request_id": request_id, "device_id": device_id, "v": "1"}


class FcmProvider:
    """FCM HTTP v1 с ключом service account. Токен доступа кэшируется до истечения."""

    def __init__(self, credentials_file: str):
        with open(credentials_file) as fh:
            self.creds = json.load(fh)
        self._token: tuple[str, float] | None = None

    def client_config(self) -> dict | None:
        """Публичные параметры клиента для приложения (FirebaseOptions) — задаются в том же JSON в разделе client (необязательно)."""
        return self.creds.get("client")

    async def _access_token(self, http: httpx.AsyncClient) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        now = int(time.time())
        key = serialization.load_pem_private_key(self.creds["private_key"].encode(), password=None)
        assertion = _jwt.encode_rs256({"iss": self.creds["client_email"], "scope": "https://www.googleapis.com/auth/firebase.messaging",
                                       "aud": self.creds.get("token_uri", "https://oauth2.googleapis.com/token"), "iat": now, "exp": now + 3600},
                                      lambda data: key.sign(data, padding.PKCS1v15(), hashes.SHA256()))
        resp = await http.post(self.creds.get("token_uri", "https://oauth2.googleapis.com/token"),
                               data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
        resp.raise_for_status()
        body = resp.json()
        self._token = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
        return self._token[0]

    async def send(self, http: httpx.AsyncClient, token: str, request_id: str, device_id: str) -> None:
        url = f"https://fcm.googleapis.com/v1/projects/{self.creds['project_id']}/messages:send"
        msg = {"message": {"token": token, "data": payload(request_id, device_id),
                           "android": {"priority": "high", "ttl": f"{settings().push_timeout}s"}}}
        resp = await http.post(url, json=msg, headers={"Authorization": f"Bearer {await self._access_token(http)}"})
        if resp.status_code == 404 or "UNREGISTERED" in resp.text:
            raise PushError("FCM: токен устройства недействителен", invalid_token=True)
        if resp.status_code >= 400:
            raise PushError(f"FCM: HTTP {resp.status_code}")


class HmsProvider:
    """Huawei Push Kit (экспериментально: не проверено на реальном аккаунте)."""

    def __init__(self, app_id: str, client_secret: str):
        self.app_id, self.client_secret = app_id, client_secret
        self._token: tuple[str, float] | None = None

    async def _access_token(self, http: httpx.AsyncClient) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        resp = await http.post("https://oauth-login.cloud.huawei.com/oauth2/v3/token",
                               data={"grant_type": "client_credentials", "client_id": self.app_id, "client_secret": self.client_secret})
        resp.raise_for_status()
        body = resp.json()
        self._token = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
        return self._token[0]

    async def send(self, http: httpx.AsyncClient, token: str, request_id: str, device_id: str) -> None:
        msg = {"validate_only": False, "message": {"data": json.dumps(payload(request_id, device_id)), "token": [token],
                                                   "android": {"urgency": "HIGH", "ttl": f"{settings().push_timeout}s"}}}
        resp = await http.post(f"https://push-api.cloud.huawei.com/v1/{self.app_id}/messages:send", json=msg,
                               headers={"Authorization": f"Bearer {await self._access_token(http)}"})
        code = resp.json().get("code") if resp.headers.get("content-type", "").startswith("application/json") else None
        if resp.status_code >= 400 or code not in ("80000000", None):
            raise PushError(f"HMS: HTTP {resp.status_code} code={code}", invalid_token=code == "80300007")


class UnifiedPushProvider:
    """UnifiedPush: POST на адрес распределителя (например, собственный ntfy). Адрес приходит от устройства, поэтому разрешены
    только хосты из MFA_GATE_PUSH_UNIFIEDPUSH_HOSTS — иначе сервер можно заставить обращаться во внутреннюю сеть (SSRF)."""

    def __init__(self, allowed_hosts: set[str]):
        self.allowed_hosts = allowed_hosts

    async def send(self, http: httpx.AsyncClient, token: str, request_id: str, device_id: str) -> None:
        url = urlparse(token)
        if url.scheme != "https" or (url.hostname or "").lower() not in self.allowed_hosts:
            raise PushError("UnifiedPush: адрес не из списка разрешённых", invalid_token=True)
        try:
            resp = await http.post(token, content=json.dumps(payload(request_id, device_id)).encode(), headers={"TTL": str(settings().push_timeout)})
        except httpx.InvalidURL:
            raise PushError("UnifiedPush: некорректный адрес", invalid_token=True) from None
        if resp.status_code in (404, 410):
            raise PushError("UnifiedPush: подписка удалена", invalid_token=True)
        if resp.status_code >= 400:
            raise PushError(f"UnifiedPush: HTTP {resp.status_code}")


def build_providers() -> dict[str, object]:
    cfg = settings()
    providers: dict[str, object] = {}
    if cfg.push_fcm_credentials_file:
        try:
            providers["fcm"] = FcmProvider(cfg.push_fcm_credentials_file)
        except (OSError, ValueError, KeyError) as exc:
            log.error("FCM не настроен: %s", type(exc).__name__)
    if cfg.push_hms_app_id and cfg.push_hms_client_secret:
        providers["hms"] = HmsProvider(cfg.push_hms_app_id, cfg.push_hms_client_secret)
    hosts = {h.strip().lower() for h in cfg.push_unifiedpush_hosts.split(",") if h.strip()}
    if hosts:
        providers["unifiedpush"] = UnifiedPushProvider(hosts)
    return providers
