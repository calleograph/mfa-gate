# MFA Gate

MFA Gate — открытый сервер двухфакторной аутентификации для VPN и внутренних приложений, который ставится на свой сервер. VPN-шлюз спрашивает MFA Gate по RADIUS. MFA Gate проверяет пароль локально или в LDAP/AD, применяет политику и запрашивает подтверждение на телефоне сотрудника.

Лицензия: [Apache-2.0](LICENSE). [English version](README.en.md).

## Возможности

- **RADIUS** (UDP 1812): PAP и Access-Challenge для одноразового кода. Message-Authenticator обязателен по умолчанию, это защита от BlastRADIUS. Повторы шлюза распознаются, второй push не отправляется.
- **Push-подтверждение.** Приложение MFA Gate Authenticator для Android подписывает решение ключом ECDSA P-256 из Android Keystore. Для подписи нужна биометрия или PIN. Сервер принимает решение только с верной подписью.
- **TOTP** по RFC 6238 (совместим с Google Authenticator), коды работают без сети.
- **LDAP / Active Directory**: несколько контроллеров, LDAPS/STARTTLS, вложенные группы, пошаговая диагностика подключения ([docs/ldap.md](docs/ldap.md)).
- **Политики**: ресурс, группы, исключения. Для каждой политики задаются действие (`mfa`, `allow`, `deny`) и разрешённые методы.
- **REST API v1** с описанием OpenAPI. Через него подключаются свои приложения: ключи API со scopes и ограничением по адресам.
- **Веб-панель**: пользователи, устройства, ресурсы, RADIUS-клиенты, политики, история входов, журнал аудита, роли администраторов. Вход в панель всегда с вторым фактором.
- **Docker Compose** и скрипт `mfagatectl`: установка, обновление с автооткатом, резервные копии, диагностика.

## Схема

```
  Сотрудник                      Сервер MFA Gate (Docker Compose)
  ┌──────────────┐  VPN   ┌──────────┐ RADIUS  ┌────────┐      ┌────────────┐
  │ VPN-клиент   │───────▶│VPN-шлюз  │────────▶│ radius │─────▶│            │
  └──────────────┘        └──────────┘ UDP 1812└────────┘      │ PostgreSQL │
                                                ┌────────┐      │            │
  Свои приложения ───── REST API v1 ───────────▶│  api   │─────▶│            │
  Администратор ─────── панель (HTTPS) ────────▶│        │      └────────────┘
                                                └────────┘            ▲
  ┌──────────────┐  Device API (HTTPS)              ▲                 │
  │ Телефон      │◀────────────────────────────────┘           ┌────────┐
  │ MFA Gate      │◀───── push-сигнал (FCM/HMS/UnifiedPush) ─────│ worker │
  │ Authenticator│                                              └────────┘
  └──────────────┘
                     radius и api ──── LDAP/AD (LDAPS) ────▶ Каталог организации
```

`api`, `radius`, `worker` и `migrate` собраны из одного образа. Панель и Device API публикуются через ваш reverse proxy с TLS. PostgreSQL наружу не открыт. Push содержит только идентификатор запроса, подробности телефон забирает сам по HTTPS.

## Быстрый старт

Нужны Linux, Docker 24+ и Docker Compose v2. Подробности: [docs/install.md](docs/install.md).

```bash
git clone https://github.com/calleograph/mfa-gate.git
cd mfagate/deploy
cp .env.example .env && chmod 600 .env
${EDITOR:-nano} .env        # MFA_GATE_PUBLIC_URL, MFA_GATE_IMAGE (образ), порты
./mfagatectl install        # пароль БД, мастер-ключ, образ, миграции, запуск
./mfagatectl admin admin    # главный администратор; пароль показывается один раз
```

Затем опубликуйте панель через reverse proxy с TLS (примеры для nginx и Caddy есть в [docs/install.md](docs/install.md)), войдите и подключите TOTP. Дальше настройка идёт по шагам из [docs/admin-guide.md](docs/admin-guide.md).

**Сохраните копию `deploy/data/secrets/master.key` отдельно от резервных копий.** Без него восстановить данные из копии нельзя.

## Состояние компонентов (0.1.0)

| Компонент | Состояние |
|---|---|
| RADIUS: PAP, Access-Challenge (TOTP), дедупликация повторов | работает; покрыто сквозными тестами на стенде (`scripts/e2e.sh`) |
| Push с подписью, TOTP, политики, REST API, журнал | работает; покрыто сквозными тестами с эмуляцией телефона |
| LDAP / OpenLDAP | покрыто тестами на OpenLDAP в Docker; **Active Directory — только юнит-тестами** |
| Доставка через FCM | реализована; требует проверки на реальном проекте Firebase и устройстве |
| HMS (Huawei Push Kit), UnifiedPush | **экспериментально**, не проверено на реальных аккаунтах |
| RADIUS Accounting, атрибуты ответа (Class, Filter-Id, VSA) | **не реализовано** |
| Режим «только второй фактор» по RADIUS (пароль проверяет шлюз) | **не реализовано**; есть только в REST API |
| Совместимость с конкретными VPN-шлюзами | **требует проверки на реальном шлюзе** ([docs/integrations.md](docs/integrations.md)) |
| Android-приложение | требует проверки на реальных устройствах ([docs/android.md](docs/android.md)) |

## Документация

| Для кого | Документ |
|---|---|
| Установка, обновление, резервные копии | [docs/install.md](docs/install.md) |
| Администратор | [docs/admin-guide.md](docs/admin-guide.md), [docs/admin-ui.md](docs/admin-ui.md) |
| Сотрудник | [docs/user-guide.md](docs/user-guide.md) |
| Подключение VPN-шлюзов и приложений | [docs/integrations.md](docs/integrations.md) |
| REST API | [docs/api.md](docs/api.md), [docs/openapi.json](docs/openapi.json) |
| Доставка push | [docs/push.md](docs/push.md) |
| LDAP / AD | [docs/ldap.md](docs/ldap.md) |
| Протокол телефона | [docs/protocol/device-api.md](docs/protocol/device-api.md) |
| Android-приложение | [android/README.md](android/README.md), [docs/android.md](docs/android.md) |
| Модель угроз, решения | [docs/threat-model.md](docs/threat-model.md), [docs/adr/](docs/adr/) |
| Разработка | [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), [CHANGELOG.md](CHANGELOG.md) |
