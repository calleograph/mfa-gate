# Доставка push-уведомлений

Push в MFA Gate — только сигнал «есть запрос». Само уведомление выглядит так:

```json
{"type": "auth_request", "request_id": "<uuid>", "v": "1"}
```

Логина, ресурса, IP и времени в нём нет. Получив сигнал, телефон сам запрашивает подробности у сервера по HTTPS (`GET /api/device/v1/requests/{id}`) и показывает уведомление. Поэтому через сервис доставки (Google, Huawei, распределитель UnifiedPush) данные о входах не проходят. Подтверждение всегда подписывается ключом из Android Keystore, сигнал push сам ничего не разрешает.

Отправкой занимается сервис `worker`: до 3 попыток на устройство. Если провайдер сообщил, что токен недействителен, токен у устройства сбрасывается, и телефон дальше работает без push, пока приложение не пришлёт новый токен.

| Способ | Сборка приложения | Состояние |
|---|---|---|
| FCM (Firebase Cloud Messaging) | `gms` | реализован; требует проверки на реальном проекте Firebase |
| Без push: long-poll и периодическая проверка | `foss` и `gms` | работает всегда |
| HMS (Huawei Push Kit) | сборки с HMS нет | экспериментально, только серверная часть |
| UnifiedPush | поддержки в приложении пока нет | экспериментально, только серверная часть |

## FCM

### Что нужно от Firebase

1. Проект Firebase организации (console.firebase.google.com) с включённым **Firebase Cloud Messaging API (V1)**.
2. Android-приложение в проекте с пакетом `mfa_gate.authenticator`. Файл `google-services.json` в сборку не кладётся: параметры клиента приходят с сервера.
3. **Service account** с правом отправки сообщений (роль «Firebase Cloud Messaging API Admin»). В консоли: Project settings → Service accounts → Generate new private key. Получится JSON с закрытым ключом, это секрет сервера.

### Формат файла

MFA Gate читает один JSON: файл service account как есть, плюс раздел `client` с **публичными** параметрами Android-приложения. Сервер передаёт `client` приложению (`GET /api/device/v1/config`), и приложение инициализирует Firebase из этих значений.

```json
{
  "type": "service_account",
  "project_id": "mfa-gate-example",
  "private_key_id": "…",
  "private_key": "<PEM закрытого ключа>",
  "client_email": "<адрес service account>",
  "token_uri": "https://oauth2.googleapis.com/token",
  "…": "остальные поля service account без изменений",

  "client": {
    "project_id": "mfa-gate-example",
    "application_id": "1:000000000000:android:0000000000000000000000",
    "api_key": "<Web API key Android-приложения>",
    "sender_id": "000000000000"
  }
}
```

Откуда взять значения `client` (Project settings → General → Your apps → Android-приложение, или из `google-services.json`):

| Поле | В `google-services.json` |
|---|---|
| `project_id` | `project_info.project_id` |
| `application_id` | `client[].client_info.mobilesdk_app_id` |
| `api_key` | `client[].api_key[].current_key` |
| `sender_id` | `project_info.project_number` |

Это идентификаторы клиента, не секреты: они и так есть в любом опубликованном приложении с Firebase. Ограничьте API-ключ в Google Cloud Console (пакет и сертификат приложения, только нужные API). Без раздела `client` сервер отправляет push, но приложение не сможет получить FCM-токен и останется на long-poll.

### Подключение файла в контейнер

Файл нужен и `api` (отдаёт `client` телефону), и `worker` (отправляет push).

```bash
cd deploy
install -m 644 /путь/к/fcm.json data/secrets/fcm.json   # каталог secrets уже 700; файл читает uid 10001 в контейнере
```

`deploy/compose.override.yml` (подхватывается `mfagatectl` автоматически, в Git не хранится):

```yaml
services:
  api:
    secrets: [master_key, fcm]
  worker:
    secrets: [master_key, fcm]

secrets:
  fcm:
    file: ${MFA_GATE_DATA:?}/secrets/fcm.json
```

В `deploy/.env`:

```
MFA_GATE_PUSH_FCM_CREDENTIALS_FILE=/run/secrets/fcm
```

Применить и проверить:

```bash
./mfagatectl start          # пересоздаёт контейнеры с новым секретом
./mfagatectl logs worker | grep 'push-провайдеры'    # ожидается: fcm
./mfagatectl diagnose       # «Доставка push» — ok
```

Телефоны, привязанные до включения FCM, получат параметры при следующем запросе `/config`. Если уведомления не приходят, откройте приложение или привяжите телефон заново.

Если файл не читается или в нём нет нужных полей, в журнале `worker` появится «FCM не настроен», а сервер продолжит работать без push.

## HMS (экспериментально)

Серверная отправка через Huawei Push Kit написана, но не проверялась на реальном аккаунте, а сборки приложения с HMS пока нет. Переменные `compose.yml` не передаёт, поэтому они задаются в `compose.override.yml`:

```yaml
services:
  api:
    environment:
      MFA_GATE_PUSH_HMS_APP_ID: "<App ID>"
      MFA_GATE_PUSH_HMS_CLIENT_SECRET: "<секрет>"   # секрет в override: права файла 600
  worker:
    environment:
      MFA_GATE_PUSH_HMS_APP_ID: "<App ID>"
      MFA_GATE_PUSH_HMS_CLIENT_SECRET: "<секрет>"
```

## UnifiedPush (экспериментально)

UnifiedPush отправляет сигнал через распределитель, который организация может развернуть сама (например, ntfy). Адрес доставки сообщает телефон. Чтобы через него нельзя было заставить сервер обращаться во внутреннюю сеть (SSRF), MFA Gate отправляет только на `https://` и только на хосты из списка:

```
MFA_GATE_PUSH_UNIFIEDPUSH_HOSTS=ntfy.example.org
```

Пустое значение выключает UnifiedPush. Поддержки в приложении пока нет.

## Без push

Push не обязателен. Без него:

- **приложение открыто**: держит long-poll (`GET /api/device/v1/requests?status=pending&wait=25`) и показывает запрос сразу;
- **приложение закрыто**: сборка `foss` проверяет запросы через WorkManager примерно раз в 15 минут. Для входа в VPN этого мало: сотруднику нужно открыть приложение после ввода пароля, пока не истёк `MFA_GATE_PUSH_TIMEOUT`.

Если push не настроен, `diagnose` показывает предупреждение «Доставка push». Для пользователей без push удобнее выдать TOTP и оставить для их ресурсов метод `totp` ([admin-guide.md](admin-guide.md#модель-политик)).
