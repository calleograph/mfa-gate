# REST API

MFA Gate предоставляет три программных интерфейса:

| Префикс | Кто вызывает | Аутентификация |
|---|---|---|
| `/api/v1/` | веб-панель, интеграции, скрипты администрирования | сессия панели или ключ API (Basic) |
| `/api/device/v1/` | приложение MFA Gate Authenticator | `Authorization: Device <device_id>:<device_secret>` |
| `/health/live`, `/health/ready` | мониторинг, healthcheck | без аутентификации |

Описание OpenAPI 3: `https://<сервер>/api/docs` (интерактивная страница) и `https://<сервер>/api/openapi.json`. Копия для версии из репозитория лежит в [openapi.json](openapi.json). Если `MFA_GATE_DOCS_ENABLED=false`, оба адреса отключены.

## Версионирование

- Номер версии стоит в пути: `/api/v1`, `/api/device/v1`. В пределах версии изменения только совместимые: новые поля в ответах, новые необязательные параметры, новые методы. Клиент должен игнорировать незнакомые поля.
- Несовместимое изменение выходит под новым префиксом (`/api/v2`), прежняя версия какое-то время работает параллельно. Об этом пишется в `CHANGELOG.md`.
- Коды причин (`reason`) и коды ошибок (`code`) стабильны и предназначены для программной обработки. Тексты `message` могут меняться.

## Аутентификация

### Ключ API (интеграции)

Ключ создаётся в панели («Ключи API») или командой на сервере из каталога `deploy/` (compose читает `deploy/.env`):

```bash
docker compose exec api python -m mfa_gate create-api-client "Портал" --scopes auth:write
```

Команда печатает `key_id:secret` один раз. На сервере хранится только хэш секрета. Запросы идут с Basic-аутентификацией:

```
Authorization: Basic base64(key_id:secret)
```

Для ключа можно задать:

- **scopes** — права (ниже);
- **ресурс** — подставляется в запросы входа без `resource_id`;
- **разрешённые адреса** (`allowed_cidrs`) — запросы с других адресов получают `403 ip_not_allowed`. Если сервер стоит за прокси, адрес клиента берётся из `X-Forwarded-For` только от `MFA_GATE_TRUSTED_PROXIES`;
- **отключение** и **ротация** секрета (`POST /api/v1/api-clients/{id}/rotate`).

CSRF-токен для ключа API не нужен.

### Сессия панели

1. `POST /api/v1/auth/login` `{"login", "password"}` → cookie `mfa_gate_session` (HttpOnly, Secure, SameSite=Strict) и `{"next": "mfa" | "totp_setup", "methods": [...], "csrf": "..."}`.
2. Второй фактор: `POST /api/v1/auth/mfa` `{"code"}` (TOTP или аварийный код) либо `POST /api/v1/auth/mfa/push` и ожидание `GET /api/v1/auth/mfa/push/{id}?wait=25`. Без фактора: `POST /api/v1/auth/totp-setup` и подтверждение.
3. Все изменяющие запросы (`POST`, `PUT`, `PATCH`, `DELETE`) передают заголовок `X-CSRF-Token` со значением `csrf`.

Сессия действует `MFA_GATE_ADMIN_SESSION_MINUTES` (30) минут и продлевается при работе. До прохождения второго фактора она живёт 10 минут и даёт доступ только к методам входа. Попытки входа ограничены: 20 в минуту с одного адреса.

### Device API

Описание протокола телефона, подписи решений и привязки — в [protocol/device-api.md](protocol/device-api.md).

## Права (scopes)

Права записываются как `<область>:read` или `<область>:write`. Роли администраторов используют те же строки ([admin-guide.md](admin-guide.md#роли-администраторов)).

| Область | Что даёт |
|---|---|
| `auth` | `auth:write` — запросы входа: `/access-requests` |
| `users` | пользователи, разблокировка |
| `devices` | устройства, ссылки привязки, TOTP |
| `groups` | группы |
| `resources` | ресурсы |
| `policies` | политики |
| `radius` | RADIUS-клиенты |
| `ldap` | каталоги LDAP/AD и их диагностика (`ldap:write`) |
| `apiclients` | ключи API |
| `requests` | история входов |
| `audit` | журнал аудита и выгрузка CSV |
| `system` | панель состояния и диагностика |

Ключу API нельзя выдать `admins:*`: назначать роли администраторов может только главный администратор в панели. Выдавайте интеграции минимальный набор прав. Для проверки входа достаточно `auth:write`.

## Ошибки

Все ошибки возвращаются одним форматом:

```json
{"code": "validation", "message": "Проверьте поля формы", "fields": [{"field": "login", "message": "Field required"}]}
```

`fields` есть только у ошибок проверки (`422 validation`).

| HTTP | `code` | Когда |
|---|---|---|
| 400 | `invalid_otp`, `bad_signature`, `invalid_public_key` | неверный код, подпись или ключ |
| 401 | `unauthenticated`, `bad_credentials`, `mfa_required`, `device_revoked` | нет или неверны учётные данные |
| 403 | `forbidden`, `csrf`, `ip_not_allowed` | нет права, нет CSRF-токена, адрес не разрешён |
| 404 | `not_found` | объекта нет или он чужой |
| 409 | `exists`, `address_in_use`, `last_superadmin`, `already_decided` | конфликт состояния |
| 410 | `expired`, `token_expired_or_used` | срок истёк |
| 422 | `validation` | ошибка в полях запроса |
| 429 | `rate_limited` | слишком много попыток |

## Пагинация

Списки пользователей (`/users`), устройств (`/devices`), истории входов (`/auth-requests`) и журнала аудита (`/audit`) принимают `page` (с 1) и `size` (1–200, по умолчанию 50):

```json
{"items": [ ... ], "total": 1234}
```

Остальные списки (ресурсы, группы, политики, RADIUS-клиенты, каталоги, ключи) короткие и возвращаются целиком, в том же формате `{"items", "total"}`.

## Основные методы `/api/v1`

| Область | Методы |
|---|---|
| Вход в панель | `POST /auth/login`, `/auth/mfa`, `/auth/mfa/push`, `GET /auth/mfa/push/{id}`, `POST /auth/totp-setup[/{factor_id}]`, `GET /auth/me`, `POST /auth/logout` |
| Запросы входа | `POST /access-requests`, `GET /access-requests/{id}?wait=0..30`, `POST /access-requests/{id}/otp` |
| Пользователи | `GET/POST /users`, `GET/PATCH/DELETE /users/{id}`, `POST /users/{id}/unlock` |
| Факторы | `POST /users/{id}/enrollment` (QR привязки), `POST /users/{id}/totp`, `POST /users/{id}/totp/{factor_id}/confirm`, `DELETE /users/{id}/totp/{factor_id}`, `GET /devices`, `POST /devices/{id}/revoke` |
| Справочники | `/groups`, `/resources`, `/policies`, `/radius-clients`, `/api-clients`, `/ldap` — `GET/POST`, `PUT/DELETE /{id}`; `POST /ldap/{id}/diagnose`, `POST /api-clients/{id}/rotate` |
| История и журнал | `GET /auth-requests`, `GET /auth-requests/problems?days=7`, `GET /audit`, `GET /audit/export.csv` |
| Состояние | `GET /dashboard`, `GET /system/diagnostics` |

Секреты (секрет RADIUS-клиента, ключа API, TOTP) возвращаются только в ответе на создание или ротацию, один раз.

## Примеры

Ключ лежит в файле параметров curl с правами 600, а не в командной строке:

```bash
# ~/.config/mfa-gate/admin.curl
user = "tf_XXXXXXXXXXXX:СЕКРЕТ"
```

Пользователи с фильтром:

```bash
curl -fsS -K ~/.config/mfa-gate/admin.curl 'https://mfa.example.org/api/v1/users?q=ivan&page=1&size=50'
```

Создать ресурс и RADIUS-клиента (нужны `resources:write`, `radius:write`):

```bash
RES=$(curl -fsS -K ~/.config/mfa-gate/admin.curl -H 'Content-Type: application/json' \
  -d '{"name": "VPN офис", "kind": "vpn"}' https://mfa.example.org/api/v1/resources | jq -r .id)

curl -fsS -K ~/.config/mfa-gate/admin.curl -H 'Content-Type: application/json' \
  -d "{\"resource_id\": \"$RES\", \"name\": \"Шлюз офиса\", \"address\": \"198.51.100.1/32\"}" \
  https://mfa.example.org/api/v1/radius-clients | jq -r .secret    # секрет показывается один раз
```

Запрос входа из своего приложения: [integrations.md](integrations.md#rest-api-для-своих-приложений).

Ошибки входа за неделю:

```bash
curl -fsS -K ~/.config/mfa-gate/admin.curl 'https://mfa.example.org/api/v1/auth-requests/problems?days=7'
```
