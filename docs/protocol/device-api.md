# Протокол мобильного устройства (Device API v1)

Контракт между сервером MFA Gate и Android-приложением. Все адреса относительно базового URL из ссылки привязки — внешнего адреса Device API `MFA_GATE_DEVICE_URL` (например, `https://auth.example.org`; по умолчанию совпадает с `MFA_GATE_PUBLIC_URL`). Наружу через reverse proxy публикуется только префикс `/api/device/` ([ADR 0015](../adr/0015-external-device-api.md)). В рабочей установке используйте HTTPS. HTTP допускается для изолированной тестовой сети и первичной настройки: приложение подключается к адресу http:// только после явного подтверждения пользователем.

## 1. Ключи и секреты устройства

| Что | Где создаётся | Где хранится | Назначение |
|---|---|---|---|
| Ключ подтверждения (ECDSA P-256) | на телефоне, Android Keystore, `setUserAuthenticationRequired(true)` | закрытый — только в Keystore, не экспортируется; открытый — на сервере (`SubjectPublicKeyInfo`, DER, base64url) | подпись решения по конкретному запросу |
| `device_secret` (32 байта, base64url) | сервер при привязке | телефон — зашифрован AES-GCM ключом Keystore; сервер — только SHA-256 | аутентификация служебных запросов (получение списка запросов, обновление push-токена) |
| Push-токен (FCM/HMS/UnifiedPush) | сервис доставки | сервер — зашифрован мастер-ключом | только сигнал «есть запрос» |

`device_secret` не даёт права подтвердить вход. Для подтверждения нужна подпись ключом из Keystore, а она требует биометрии или PIN устройства.

## 2. Привязка устройства

Администратор (или пользователь в портале) выпускает одноразовую ссылку привязки и показывает её QR-кодом:

```
mfagate://enroll?v=1&url=<base64url(BASE_URL)>&token=<TOKEN>&label=<base64url(логин@сервер)>
```

- `TOKEN` — 32 случайных байта, base64url; одноразовый; срок жизни по умолчанию 24 часа; на сервере хранится только SHA-256.
- Приложение показывает пользователю адрес сервера и `label` и просит подтвердить привязку.

```
POST /api/device/v1/enroll
Content-Type: application/json

{
  "token": "<TOKEN>",
  "device_name": "Pixel 8",
  "platform": "android",
  "app_version": "0.1.0",
  "public_key": "<base64url SPKI DER>",
  "push": {"provider": "fcm" | "hms" | "unifiedpush" | "none", "token": "<строка или null>"}
}

200 OK
{
  "device_id": "<uuid>",
  "device_secret": "<base64url>",
  "user": {"login": "ivanov", "display_name": "Иванов И."},
  "server": {"name": "MFA Gate", "url": "https://auth.example.org"},
  "poll_interval": 25
}
```

Ошибки: `400 invalid_public_key`, `410 token_expired_or_used`, `429 rate_limited`.

## 3. Аутентификация служебных запросов

```
Authorization: Device <device_id>:<device_secret>
```

Сервер сравнивает SHA-256 секрета за постоянное время. Отозванное устройство получает `401 device_revoked`; приложение должно удалить учётную запись локально.

## 4. Получение запросов

```
GET /api/device/v1/requests?status=pending&wait=25
```

`wait` (0–30 секунд) — long-poll: ответ приходит сразу при появлении запроса или по истечении ожидания с пустым списком. Без push-сервиса приложение на экране держит этот long-poll постоянно.

```
200 OK
{
  "items": [
    {
      "id": "<uuid>",
      "login": "ivanov",
      "resource": "VPN офис",
      "source_ip": "203.0.113.10",
      "location": null,
      "client": "OpenVPN",
      "created_at": "2026-11-18T21:00:00Z",
      "expires_at": "2026-11-18T21:01:00Z",
      "challenge": "<base64url 32 байта>",
      "status": "pending"
    }
  ]
}
```

Ответ содержит `server_time` (ISO 8601, UTC): оставшееся время считайте как `expires_at − server_time`, а не по часам телефона. Если pending-запросы уже есть, ответ приходит сразу — перед следующим long-poll клиент делает паузу (2–3 с), пока пользователь не принял решение.

`GET /api/device/v1/requests?limit=50` — история запросов этого пользователя (без `challenge` для завершённых).
`GET /api/device/v1/requests/{id}` — один запрос (по push-уведомлению), тоже с `server_time`.

## 5. Решение по запросу

Строка для подписи — UTF-8, строки разделены `\n`, без завершающего перевода строки:

```
mfa-gate-decision-v1
<request_id>
<device_id>
<challenge>
<decision>
```

`decision` — `approve` или `deny`. Подпись — `SHA256withECDSA`, DER, base64url.

```
POST /api/device/v1/requests/{id}/decision
Authorization: Device <device_id>:<device_secret>

{"decision": "approve", "signature": "<base64url DER>"}

200 OK  {"status": "approved"}
```

Сервер принимает решение, только если выполнено всё сразу:
- устройство активно и принадлежит пользователю запроса;
- запрос в статусе `pending` и не истёк;
- подпись верна для открытого ключа устройства;
- статус меняется атомарно (`UPDATE ... WHERE status = 'pending'`). Повторная отправка той же подписи получает `409 already_decided`.

Ошибки: `400 bad_signature`, `404 not_found` (в том числе чужой запрос), `409 already_decided`, `410 expired`.

## 6. Push-уведомление

Содержит только сигнал, без данных о входе:

```json
{"type": "auth_request", "request_id": "<uuid>", "device_id": "<uuid>", "v": "1"}
```

`device_id` позволяет приложению с несколькими учётными записями сразу выбрать нужный сервер. Получив сигнал, приложение запрашивает `GET /api/device/v1/requests/{id}` по HTTPS и показывает уведомление с действиями «Подтвердить» / «Отклонить». Подтверждение всегда открывает биометрический запрос.

## 7. Параметры доставки push

Сборка `gms` приложения не содержит `google-services.json`: параметры Firebase-проекта организации приходят с сервера, и приложение инициализирует `FirebaseApp` вручную (`FirebaseOptions.Builder`). Сборка `foss` работает без Google: long-poll на экране и периодическая проверка через WorkManager.

```
GET /api/device/v1/config          (также поле "push_config" в ответе /enroll)
200 OK
{
  "push_config": {
    "fcm": {"project_id": "...", "application_id": "...", "api_key": "...", "sender_id": "..."} | null
  },
  "poll_interval": 25
}
```

Это публичные идентификаторы клиента Firebase, а не секреты; закрытый ключ service account остаётся только на сервере.

## 8. Прочее

- Ошибки всегда в формате `{"code": "<машинный код>", "message": "<текст для пользователя>"}`; у ошибок валидации (422) ещё `fields: [{field, message}]`.
- Главный адрес сервера — из QR привязки; `server.url` в ответе `/enroll` справочный.

- `PUT /api/device/v1/push` — `{"provider": "...", "token": "..."}`, обновление push-токена.
- `DELETE /api/device/v1/self` — отвязка устройства пользователем.
- `GET /api/device/v1/ping` — проверка связи и состояния устройства (`{"status": "active"}`).

## 9. TOTP

TOTP в приложении работает независимо от Device API и без сети: стандартный `otpauth://totp/<label>?secret=<BASE32>&issuer=<issuer>&algorithm=SHA1&digits=6&period=30` (RFC 6238, совместимо с Google Authenticator). Секреты хранятся на телефоне в зашифрованном виде (AES-GCM, ключ в Keystore).
