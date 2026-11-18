# Интеграция по REST API

Если ваше приложение (портал, внутренний сервис, скрипт) само принимает логин и пароль, оно может спросить MFA Gate через REST API. RADIUS для этого не нужен.

Интерактивное описание всех методов (OpenAPI): **`<адрес сервера>/api/docs`**, например `https://mfa.example.org/api/docs`. Ссылка есть и в панели — значок книги в подвале боковой панели. Справочник (аутентификация, права, ошибки, пагинация): [docs/api.md](https://github.com/calleograph/mfa-gate/blob/main/docs/api.md).

## Шаг 1. Ресурс и ключ API

1. «Ресурсы» → «Добавить ресурс», тип «Приложение», например `Портал`.
2. «API-клиенты» → «Создать ключ»: «Права ключа» — только `auth:write`, «Ресурс по умолчанию» — `Портал`, «Разрешённые адреса» — адрес сервера приложения.
3. Скопируйте «Идентификатор ключа (key_id)» и «Секрет» — секрет показывается один раз.

Сохраните ключ в файле с правами 600 на сервере приложения, а не в командной строке (аргументы видны другим пользователям в списке процессов):

```bash
install -m 600 /dev/null ~/.config/mfa-gate.curl
printf 'user = "%s"\n' 'tf_xxxxxxxxxxxx:СЕКРЕТ' > ~/.config/mfa-gate.curl
```

## Шаг 2. Запрос входа

```bash
curl -fsS -K ~/.config/mfa-gate.curl -H 'Content-Type: application/json' \
  -d '{"login": "ivanov", "password": "<пароль>", "source_ip": "203.0.113.25", "client_name": "Портал"}' \
  https://mfa.example.org/api/v1/access-requests
```

**Ожидаемый результат** (у пользователя привязан телефон):

```json
{"id": "6f1c…", "status": "pending", "reason": null, "message": "", "method": "push", "expires_at": "…"}
```

## Шаг 3. Ожидание решения

```bash
curl -fsS -K ~/.config/mfa-gate.curl "https://mfa.example.org/api/v1/access-requests/6f1c…?wait=30"
```

Запрос ждёт до 30 секунд. Повторяйте, пока `status` = `pending` и не наступил `expires_at`.

| `status` | Что делать приложению |
|---|---|
| `accepted` | пустить пользователя |
| `rejected` | отказать; `reason` — код причины ([docs/admin-guide.md](https://github.com/calleograph/mfa-gate/blob/main/docs/admin-guide.md#история-входов)) |
| `pending` | ждать решения на телефоне |
| `challenge` | спросить код TOTP и отправить `POST /api/v1/access-requests/{id}/otp` с `{"code": "123456"}` |

**Как проверить:** подтвердите запрос на телефоне — ответ `"status": "accepted"`; в «Истории входов» запись с каналом `api` и ресурсом `Портал`.

## Полезно знать

- Если приложение уже проверило пароль само, не передавайте `password` — MFA Gate проверит только второй фактор (пользователь должен быть ему известен).
- Код TOTP можно передать сразу в поле `otp` первого запроса.
- Ключ видит только свои запросы; ключ, привязанный к ресурсу, не может запросить вход в другой ресурс.
- Готовый пример на bash — скрипт для OpenVPN [`integrations/openvpn/mfa-gate-openvpn.sh`](https://github.com/calleograph/mfa-gate/blob/main/integrations/openvpn/mfa-gate-openvpn.sh) ([Подключение VPN](Подключение-VPN.md)).
- Управлять MFA Gate (пользователи, ресурсы, RADIUS-клиенты) через API тоже можно — нужны соответствующие права ключа, примеры в [docs/api.md](https://github.com/calleograph/mfa-gate/blob/main/docs/api.md#примеры).
