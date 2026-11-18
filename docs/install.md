# Установка и эксплуатация

## Требования

- Linux x86_64 с Docker Engine 24+ и плагином Docker Compose v2 (`docker compose version`).
- 1 vCPU, 1 ГБ ОЗУ, 5 ГБ диска (база растёт с журналом: срок хранения 365 дней для аудита и 180 дней для истории входов).
- Пользователь в группе `docker` или `sudo`. Нужны `openssl`, `curl`, `ss` (пакет iproute2).
- DNS-имя для панели и Device API (например, `mfa.example.org`) и reverse proxy с TLS. Телефоны в рабочей сборке подключаются только по HTTPS.
- VPN-шлюзы должны достучаться до сервера по UDP 1812 (или другому порту из `.env`).

## Состав

`deploy/compose.yml` поднимает пять сервисов из одного образа `mfa-gate`:

| Сервис | Назначение | Наружу |
|---|---|---|
| `postgres` | PostgreSQL 16 | нет |
| `migrate` | применяет миграции и завершается; остальные стартуют после него | нет |
| `api` | панель, REST API, Device API | `MFA_GATE_HTTP_BIND:MFA_GATE_HTTP_PORT` → 8080/tcp (по умолчанию `127.0.0.1:18480`) |
| `radius` | RADIUS-сервер | `MFA_GATE_RADIUS_BIND:MFA_GATE_RADIUS_PORT` → 1812/udp |
| `worker` | доставка push, истечение запросов, очистка по срокам хранения | нет |

Контейнеры работают с файловой системой только для чтения, без capabilities, от uid 10001. Данные лежат в `MFA_GATE_DATA` (по умолчанию `deploy/data`): `postgres/` и `secrets/master.key`.

## Установка на чистый сервер

```bash
git clone https://github.com/calleograph/mfa-gate.git
cd mfagate/deploy
cp .env.example .env && chmod 600 .env
```

Проверьте в `.env`:

- `MFA_GATE_PUBLIC_URL` — внешний HTTPS-адрес (`https://mfa.example.org`). Он попадает в QR-код привязки телефона, поэтому его надо исправить до привязки.
- `MFA_GATE_IMAGE` и `MFA_GATE_VERSION`. Опубликованный образ: `ghcr.io/<владелец>/mfa-gate` и версия выпуска. Чтобы собрать образ локально из этого каталога, укажите `MFA_GATE_IMAGE=mfa-gate` (имя без `/`).
- порты `MFA_GATE_HTTP_PORT` и `MFA_GATE_RADIUS_PORT`.

Затем запустите `./mfagatectl install`. Скрипт:

1. проверит Docker, сгенерирует `POSTGRES_PASSWORD`, проверит свободные порты;
2. создаст `data/secrets/master.key` (32 случайных байта, base64), если его нет;
3. загрузит или соберёт образ;
4. запустит сервисы (миграции выполняет `migrate`) и дождётся статуса healthy (до 3 минут).

`install` можно запускать повторно: существующие `.env`, ключ и данные не перезаписываются.

**Сразу сохраните копию `data/secrets/master.key` вне сервера** (менеджер паролей, сейф). Ключом зашифрованы секреты TOTP, RADIUS и LDAP и push-токены. Резервные копии базы без него бесполезны, а новый ключ старые данные не расшифрует.

## Установка рядом с другими проектами

Все имена контейнеров, сетей и томов строятся из `COMPOSE_PROJECT_NAME`, порты хоста задаются только явно. Второй экземпляр или соседний проект не конфликтуют, если:

- `COMPOSE_PROJECT_NAME` уникален (`mfagate`, `mfa-gate-test` …);
- `MFA_GATE_HTTP_PORT` и `MFA_GATE_RADIUS_PORT` свободны (`install` проверяет и откажется запускаться на занятом порту);
- `MFA_GATE_DATA` и `MFA_GATE_BACKUPS` у каждого экземпляра свои;
- HTTP опубликован только на `127.0.0.1`, наружу — через общий reverse proxy.

Если `.env` ещё нет, `install` создаёт его из `.env.example` и сразу продолжает установку. Значения можно передать через окружение, они запишутся в новый `.env`:

```bash
COMPOSE_PROJECT_NAME=mfa-gate-test MFA_GATE_HTTP_PORT=18481 MFA_GATE_RADIUS_PORT=11812 \
MFA_GATE_PUBLIC_URL=https://mfa-test.example.org ./mfagatectl install
```

Для экземпляра с отдельным файлом окружения задайте `MFA_GATE_ENV_FILE=/путь/к/.env` перед каждой командой `mfagatectl`.

## Reverse proxy с TLS

Через прокси публикуются панель (`/`), REST API (`/api/v1/`) и Device API (`/api/device/v1/`). Учтите:

- MFA Gate берёт адрес клиента из **последнего** значения `X-Forwarded-For` и только если соединение пришло с адреса из `MFA_GATE_TRUSTED_PROXIES`. Прокси должен перезаписывать заголовок, а не передавать значение клиента.
- Прокси на том же хосте подключается к опубликованному порту `127.0.0.1:18480`, но контейнер видит адрес шлюза docker-сети, а не `127.0.0.1`. Поэтому в `.env.example` в доверенные добавлена сеть `172.16.0.0/12`. Узнать точную подсеть проекта можно командой `docker network inspect mfa_gate_backend -f '{{(index .IPAM.Config 0).Subnet}}'` и сузить список до неё.
- Long-poll держит запрос до 30 секунд, поэтому таймаут чтения у прокси должен быть не меньше 60 секунд.
- Cookie панели выдаётся с флагом `Secure` (`MFA_GATE_COOKIE_SECURE=true`), поэтому вход работает только по HTTPS.

### nginx

```nginx
server {
    listen 443 ssl;
    http2 on;
    server_name mfa.example.org;

    ssl_certificate     /etc/letsencrypt/live/mfa.example.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mfa.example.org/privkey.pem;

    client_max_body_size 1m;

    location / {
        proxy_pass http://127.0.0.1:18480;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;   # перезаписать, не дописывать к значению клиента
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 75s;                          # long-poll до 30 с
        proxy_buffering off;
    }
}

server {
    listen 80;
    server_name mfa.example.org;
    return 301 https://$host$request_uri;
}
```

### Caddy

Caddy сам получает сертификат и по умолчанию заменяет `X-Forwarded-For` адресом клиента (входящий заголовок не доверяется).

```caddyfile
mfa.example.org {
    reverse_proxy 127.0.0.1:18480
}
```

Проверка: `curl -fsS https://mfa.example.org/health/ready` должен вернуть `{"status":"ok",...}`.

## Firewall для RADIUS

UDP 1812 должен быть доступен **только адресам VPN-шлюзов**. MFA Gate и так молча отбрасывает пакеты от адресов, не заведённых как RADIUS-клиенты, но открытый порт лишний раз показывает сервис снаружи.

Порты, опубликованные Docker, обходят правила `ufw` и цепочку `INPUT`. Фильтровать нужно в цепочке `DOCKER-USER` по исходному порту назначения:

```bash
# сначала запрет для всех, затем разрешение шлюзу (вставка -I ставит правило выше)
iptables -I DOCKER-USER -p udp -m conntrack --ctorigdstport 1812 --ctdir ORIGINAL -j DROP
iptables -I DOCKER-USER -p udp -m conntrack --ctorigdstport 1812 --ctdir ORIGINAL -s 198.51.100.10 -j ACCEPT
# сохранить после перезагрузки: iptables-persistent (netfilter-persistent save) или ваш способ
```

Другой вариант: укажите в `MFA_GATE_RADIUS_BIND` адрес внутреннего интерфейса, на котором шлюзы видят сервер, вместо `0.0.0.0`. Если `MFA_GATE_RADIUS_PORT` не 1812, подставьте его в правила. Правила проверьте на своём хосте: при nftables-бэкенде и других настройках Docker они могут отличаться.

## Первый администратор

```bash
./mfagatectl admin admin
```

Команда создаёт локального главного администратора (`superadmin`) и печатает пароль один раз. Если такой администратор уже есть, команда сбрасывает ему пароль. Откройте `https://mfa.example.org`, войдите. Панель сразу попросит подключить TOTP: отсканируйте QR-код приложением MFA Gate Authenticator или любым TOTP-приложением и введите код. Без второго фактора панель не открывается ни для одной роли.

Если администратор потерял второй фактор, выдайте ему аварийный код: `./mfagatectl emergency <логин>` ([admin-guide.md](admin-guide.md#аварийный-доступ)).

## Переменные окружения

Файл `deploy/.env` читает `mfagatectl` и `compose.yml`:

| Переменная | Назначение |
|---|---|
| `COMPOSE_PROJECT_NAME` | имя экземпляра (префикс контейнеров, сетей) |
| `MFA_GATE_IMAGE`, `MFA_GATE_VERSION` | образ и зафиксированная версия; имя без `/` — локальная сборка |
| `MFA_GATE_REPO` | `владелец/репозиторий` на GitHub для `check-update` |
| `MFA_GATE_DATA`, `MFA_GATE_BACKUPS` | каталоги данных и резервных копий |
| `MFA_GATE_HTTP_BIND`, `MFA_GATE_HTTP_PORT` | публикация HTTP на хосте |
| `MFA_GATE_RADIUS_BIND`, `MFA_GATE_RADIUS_PORT` | публикация RADIUS на хосте |
| `POSTGRES_PASSWORD` | генерируется установщиком |

Настройки приложения (`server/mfa_gate/config.py`). Переменные, отмеченные «override», `compose.yml` в контейнеры не передаёт. Чтобы их задать, нужен `deploy/compose.override.yml` (пример в [push.md](push.md#подключение-файла-в-контейнер)).

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `MFA_GATE_PUBLIC_URL` | — | внешний адрес для телефонов (QR привязки) |
| `MFA_GATE_SERVER_NAME` | `MFA Gate` | имя в приложении и в TOTP |
| `MFA_GATE_PUSH_TIMEOUT` | `45` | секунд ожидания ответа на push |
| `MFA_GATE_TRUSTED_PROXIES` | `127.0.0.1/32,::1/128` | от кого принимать `X-Forwarded-For` |
| `MFA_GATE_COOKIE_SECURE` | `true` | cookie панели только по HTTPS |
| `MFA_GATE_LOG_LEVEL` | `INFO` | уровень журнала |
| `MFA_GATE_RADIUS_REQUIRE_MESSAGE_AUTHENTICATOR` | `true` | отбрасывать Access-Request без Message-Authenticator |
| `MFA_GATE_PUSH_FCM_CREDENTIALS_FILE` | пусто | путь к JSON service account FCM внутри контейнера ([push.md](push.md)) |
| `MFA_GATE_PUSH_UNIFIEDPUSH_HOSTS` | пусто | разрешённые хосты UnifiedPush |
| `MFA_GATE_MASTER_KEY_FILE` / `MFA_GATE_MASTER_KEY` | задаёт compose | мастер-ключ (файл или значение) |
| `MFA_GATE_DATABASE_URL` | задаёт compose | строка подключения к PostgreSQL |
| `MFA_GATE_OTP_CHALLENGE_TIMEOUT` | `120` | override: секунд на ввод кода после Access-Challenge |
| `MFA_GATE_ENROLLMENT_TTL_HOURS` | `24` | override: срок ссылки привязки телефона |
| `MFA_GATE_ADMIN_SESSION_MINUTES` | `30` | override: сессия панели (продлевается при работе) |
| `MFA_GATE_LOCKOUT_THRESHOLD`, `MFA_GATE_LOCKOUT_MINUTES` | `8`, `15` | override: блокировка после ошибок входа |
| `MFA_GATE_PUSH_HMS_APP_ID`, `MFA_GATE_PUSH_HMS_CLIENT_SECRET` | пусто | override: Huawei Push Kit (экспериментально) |
| `MFA_GATE_AUDIT_RETENTION_DAYS` | `365` | override: срок хранения журнала аудита |
| `MFA_GATE_AUTH_REQUEST_RETENTION_DAYS` | `180` | override: срок хранения истории входов |
| `MFA_GATE_DOCS_ENABLED` | `true` | override: `/api/docs` и `/api/openapi.json` |

## Обновление

```bash
./mfagatectl check-update      # нужен MFA_GATE_REPO в .env
./mfagatectl update 0.2.0
```

`update` делает следующее:

1. **Копия.** Резервная копия базы `backups/<время>-pre-0.2.0` и запись о предыдущей версии в `backups/last-update`.
2. **Образ.** `MFA_GATE_VERSION` меняется на новую версию, образ загружается (или собирается локально). Если образ недоступен, версия возвращается в `.env`, а система не меняется.
3. **Миграции.** `docker compose up -d`: сервис `migrate` применяет миграции, затем стартуют `api`, `radius`, `worker`.
4. **Проверка.** Скрипт ждёт healthy всех трёх сервисов до 3 минут.
5. **Автооткат.** Если запуск или проверка не прошли, выполняется `rollback`: прежняя версия и база из копии шага 1.

При локальной сборке (`MFA_GATE_IMAGE` без `/`) сначала переключите рабочую копию на нужный тег (`git fetch --tags && git checkout v0.2.0`): образ собирается из каталога `server/`.

Читайте `CHANGELOG.md` перед обновлением. Версии `0.x` могут менять настройки.

## Откат

```bash
./mfagatectl rollback
```

Откат возвращает версию и базу из копии, сделанной последним `update`. Миграции вперёд необратимы, поэтому база восстанавливается целиком: изменения после обновления (новые пользователи, привязки, журнал) теряются.

## Резервные копии и восстановление

```bash
./mfagatectl backup                       # → backups/<время>-manual/{db.dump, SHA256SUMS, manifest}
./mfagatectl restore backups/20261118T210000Z-manual
```

- Копия — `pg_dump` в формате custom, контрольная сумма и манифест: версия, ревизия схемы, отпечаток мастер-ключа.
- **Мастер-ключ в копию не входит.** Храните его отдельно и в другом месте, чем копии: копия и ключ вместе дают доступ ко всем секретам.
- `restore` проверяет контрольную сумму и что текущий `master.key` совпадает с отпечатком из манифеста. Затем восстанавливает базу во временную, подменяет рабочую, а прежнюю оставляет как `mfa_gate_prev` до следующего восстановления.
- Копии лежат на том же сервере. Регулярно переносите их на другой носитель.

Ежедневная копия через cron (путь к каталогу подставьте свой):

```cron
30 2 * * * cd /opt/mfa-gate/deploy && ./mfagatectl backup daily >/dev/null 2>&1
```

Удаление старых копий `mfagatectl` не делает, настройте его сами (например, `find backups -maxdepth 1 -name '*-daily' -mtime +14 -exec rm -rf {} +`).

**Перенос на новый сервер:** склонируйте репозиторий, положите прежний `master.key` в `data/secrets/`, перенесите значения из `.env`, выполните `./mfagatectl install`, затем `./mfagatectl restore <копия>`.

## Диагностика

```bash
./mfagatectl status            # контейнеры, версия, образ
./mfagatectl logs radius -f    # журнал сервиса (api, radius, worker, migrate, postgres)
./mfagatectl diagnose          # проверки приложения + архив backups/diag-<время>.tar.gz
./mfagatectl radius-test ivanov
```

`diagnose` проверяет базу, миграции, мастер-ключ, пульс `radius` и `worker`, настройку push, публичный адрес, RADIUS-клиенты и каталоги LDAP. В архив для поддержки попадают сводка, последние 300 строк журналов и `.env` с замаскированными паролями и ключами. Если значение секрета встретится в архиве, архив не создаётся.

`radius-test` отправляет Access-Request из контейнера `api` в `radius` и спрашивает секрет и пароль. Чтобы пакет не был отброшен, заведите RADIUS-клиент на подсеть docker-сети проекта: `docker network inspect mfa_gate_backend -f '{{(index .IPAM.Config 0).Subnet}}'`. После проверки отключите или удалите этот клиент.

Частые причины проблем:

| Признак | Что проверить |
|---|---|
| шлюз получает таймаут, в журнале `radius` «пакет от неизвестного клиента» | адрес RADIUS-клиента в панели совпадает с адресом, с которого приходят пакеты (NAT!) |
| в аудите `radius.bad_authenticator` | неверный общий секрет или шлюз не отправляет Message-Authenticator |
| в аудите `auth.reject` с причиной `push_expired` | таймаут шлюза меньше `MFA_GATE_PUSH_TIMEOUT`, телефон не получает push |
| в журнале входа адрес прокси вместо адреса клиента | `MFA_GATE_TRUSTED_PROXIES` не содержит адрес прокси |
| `diagnose`: «Доставка push — warning» | push не настроен, телефоны работают только с открытым приложением ([push.md](push.md)) |

## Удаление

```bash
./mfagatectl stop
docker compose --project-directory . --env-file .env -f compose.yml -p mfagate down --remove-orphans
# данные postgres принадлежат uid контейнера — удаляются через контейнер или sudo
docker run --rm -v "$PWD:/w" alpine:3 rm -rf /w/data /w/backups
```

Перед удалением сделайте копию, если данные понадобятся. Образы удаляются отдельно (`docker image ls | grep mfa-gate`). Удалите и правила firewall для RADIUS.
