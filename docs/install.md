# Установка и эксплуатация — справочник

Справочник по составу установки, переменным окружения и командам `mfagatectl`. Пошаговые инструкции — в Wiki (копия в репозитории: [`wiki/`](../wiki/Home.md)):

| Задача | Пошагово |
|---|---|
| Установка All-in-One | [Быстрая установка All-in-One](../wiki/Быстрая-установка-All-in-One.md) |
| Требования и проверка сервера | [Требования к серверу](../wiki/Требования-к-серверу.md) |
| HTTPS через nginx или Caddy | [HTTPS через reverse proxy](../wiki/HTTPS-через-reverse-proxy.md) |
| Обновление и откат | [Обновление](../wiki/Обновление.md) |
| Резервные копии, расписание | [Резервное копирование](../wiki/Резервное-копирование.md) |
| Восстановление, перенос на другой сервер | [Восстановление](../wiki/Восстановление.md) |
| Типовые проблемы | [Диагностика](../wiki/Диагностика.md) |
| Удаление | [Удаление и повторная установка](../wiki/Удаление-и-повторная-установка.md) |

## Состав

`deploy/compose.yml` поднимает пять сервисов. Четыре из них собраны из одного образа `ghcr.io/calleograph/mfa-gate:<версия>`:

| Сервис | Назначение | Публикуется на хосте |
|---|---|---|
| `postgres` | PostgreSQL 16 (`postgres:16-alpine`), база и пользователь `mfagate` | нет |
| `migrate` | применяет миграции и завершается; остальные стартуют после него | нет |
| `api` | личный кабинет `/`, панель `/admin/`, REST API `/api/v1`, Device API `/api/device/v1`, `/health/live`, `/health/ready`, OpenAPI `/api/docs` | `MFA_GATE_HTTP_BIND:MFA_GATE_HTTP_PORT` → 8080/tcp |
| `radius` | RADIUS-сервер | `MFA_GATE_RADIUS_BIND:MFA_GATE_RADIUS_PORT` → 1812/udp |
| `worker` | доставка push, истечение запросов, очистка по срокам хранения | нет |

Контейнеры приложения: файловая система только для чтения, `cap_drop: ALL`, `no-new-privileges`, uid 10001. Журналы Docker ограничены 5 файлами по 10 МБ на сервис. Проверки готовности: `api` — `GET /health/ready`, `radius` и `worker` — файл пульса, который обновляется, пока процесс жив и видит базу.

Имена контейнеров, сетей и томов строятся из `COMPOSE_PROJECT_NAME`; сеть экземпляра — `<COMPOSE_PROJECT_NAME>_backend` (по умолчанию `mfa-gate_backend`).

## Файлы и каталоги

| Путь | Содержимое | Права |
|---|---|---|
| `deploy/.env` | конфигурация экземпляра, пароль базы | 600, в Git не хранится |
| `deploy/data/postgres/` | файлы PostgreSQL | владелец — пользователь контейнера postgres |
| `deploy/data/secrets/master.key` | мастер-ключ (32 байта, base64) | каталог 700, файл 644 (читает uid 10001 в контейнере) |
| `deploy/backups/` | копии `<время UTC>-<метка>/{db.dump,SHA256SUMS,manifest}`, `last-update`, архивы `diag-*.tar.gz` | 700 |
| `deploy/compose.override.yml` | локальные дополнения (файл FCM как Docker secret); подхватывается `mfagatectl` автоматически | в Git не хранится |
| `/usr/local/bin/mfagatectl` | ссылка на `deploy/mfagatectl`, создаёт `install.sh` | — |
| `/var/log/mfa-gate/install-<дата>-<время>.log` | журнал каждого запуска `install.sh` (без паролей) | каталог 700, файл 600 |

`install.sh` передаёт каталог установки root (`chown -R root:root`, без права записи для группы и остальных): скрипты из него выполняются от root. Обновление исходников после этого — `sudo git -C <каталог> pull` или `sudo git -C <каталог> checkout vX.Y.Z`.

## install.sh

```bash
git clone https://github.com/calleograph/mfa-gate.git && cd mfa-gate && sudo ./install.sh
```

Только Ubuntu и Debian (x86_64, aarch64). Этапы:

| Этап | Проверки и действия | Остановка, если |
|---|---|---|
| Проверка сервера | ОС, архитектура, память, диск, Интернет | не Ubuntu/Debian; < 900 МБ ОЗУ; < 5 ГБ свободно; нет доступа ни к `ghcr.io`, ни к `github.com` |
| Docker и необходимые пакеты | `curl`, `openssl`, `iproute2`; Docker из пакетов ОС (`docker.io`, `docker-compose-v2`), иначе скриптом `get.docker.com`; `systemctl enable --now docker` | Docker не запустился; нет Compose v2 |
| Конфигурация | новый `deploy/.env` из `deploy/.env.example`: `COMPOSE_PROJECT_NAME`, `MFA_GATE_VERSION` (из `VERSION`), HTTP-порт (первый свободный 8080…8100), RADIUS-порт, `MFA_GATE_PUBLIC_URL=http://<IP>:<порт>`, `POSTGRES_PASSWORD`. Существующий `.env` сохраняется | заданный порт занят; UDP-порт RADIUS занят; `.env` с синтаксической ошибкой |
| Образы, база данных и запуск | `mfagatectl install`: мастер-ключ, образ, миграции, запуск, ожидание healthy | сервисы не стали healthy за 3 минуты |
| Первый администратор | `mfagatectl admin-init`: `admin` с временным паролем, если активного главного администратора нет; пароль выводится только на экран | — |
| Проверка работы | `/health/ready`, главная страница панели, прослушивание UDP-порта RADIUS; ссылка `/usr/local/bin/mfagatectl` | любая проверка не прошла |

Переопределения при первом запуске (записываются в новый `.env`, при существующем `.env` игнорируются): `MFA_GATE_HTTP_PORT`, `MFA_GATE_RADIUS_PORT`, `MFA_GATE_PUBLIC_URL`, `COMPOSE_PROJECT_NAME`.

```bash
sudo MFA_GATE_HTTP_PORT=8180 MFA_GATE_RADIUS_PORT=11812 MFA_GATE_PUBLIC_URL=http://mfa.example.org:8180 ./install.sh
```

Повторный запуск идемпотентен: `.env`, мастер-ключ, данные и пароль администратора сохраняются.

## Команды mfagatectl

Запуск: `sudo mfagatectl <команда>` (или `sudo <каталог>/deploy/mfagatectl <команда>`). Файл окружения по умолчанию — `deploy/.env` рядом со скриптом; другой задаётся `MFA_GATE_ENV_FILE=/путь/.env`.

| Команда | Что делает |
|---|---|
| `install` | первая или повторная установка без `install.sh`: создаёт `.env` из примера (если его нет; без заданного `MFA_GATE_PUBLIC_URL` останавливается, чтобы вы проверили `.env`), генерирует пароль базы и мастер-ключ, проверяет порты, загружает или собирает образ, запускает, ждёт healthy (до 3 мин) |
| `status` | `docker compose ps`: сервис, состояние, порты; версия и образ |
| `logs [сервис] [-f]` | последние 200 строк журнала всех или одного сервиса (`api`, `radius`, `worker`, `migrate`, `postgres`); `-f` — слежение |
| `start` | `docker compose up -d` (пересоздаёт контейнеры после изменения `.env` или `compose.override.yml`) и ожидание healthy (до 2 мин) |
| `stop` | остановка всех сервисов без удаления |
| `restart [сервис]` | перезапуск всех или одного сервиса (без перечитывания `.env`; для новых настроек — `start`) |
| `update <X.Y.Z>` | копия базы `pre-<версия>` → смена `MFA_GATE_VERSION` → загрузка (или сборка из `server/`) образа → `up -d` с миграциями → ожидание healthy до 3 мин → при сбое `rollback`. Если образ недоступен, версия возвращается, система не меняется |
| `rollback` | возврат к версии и копии из `backups/last-update` (последний `update`); база восстанавливается целиком |
| `check-update` | последняя версия на GitHub (`MFA_GATE_REPO`, через `api.github.com`) |
| `backup [метка]` | `pg_dump -Fc` в `backups/<время>-<метка>` (по умолчанию `manual`) с `SHA256SUMS` и `manifest` (версия, ревизия схемы, время, первые 16 символов SHA-256 мастер-ключа); печатает путь. Мастер-ключ в копию не входит |
| `restore <каталог>` | проверка контрольной суммы и отпечатка мастер-ключа → остановка `api`/`radius`/`worker` → восстановление во временную базу → подмена рабочей (прежняя остаётся как `mfa_gate_prev`) → запуск и ожидание healthy |
| `diagnose` | сводка (версии, контейнеры, проверки приложения, `/health/ready`, публичный адрес, диск, копии) и архив `backups/diag-<время>.tar.gz`: сводка, 300 строк журнала каждого сервиса, `.env` с замаскированными `*PASSWORD*`/`*SECRET*`/`*KEY*`. Если значение пароля базы или мастер-ключа встречается в архиве, архив не создаётся |
| `admin <логин>` | создаёт локального главного администратора или сбрасывает пароль существующему локальному (снимает блокировку); пароль печатается один раз |
| `admin-init` | `admin` с временным паролем (смена при первом входе), только если активного главного администратора нет |
| `emergency <логин>` | одноразовый аварийный код на 15 минут вместо второго фактора при входе администратора в панель; события `admin.emergency_code_issued` и `admin.emergency_access` уровня `critical` |
| `radius-test <логин>` | Access-Request из контейнера `api` в `radius` (спрашивает секрет и пароль); печатает JSON `result`, `authenticator_ok`, `message`, `seconds`. Нужен RADIUS-клиент на подсеть `<проект>_backend` |
| `uninstall` | `docker compose down`: контейнеры удалены, данные, копии и `.env` сохранены |
| `uninstall --purge` | после ввода `DELETE`: контейнеры, тома, всё содержимое `MFA_GATE_DATA` и `MFA_GATE_BACKUPS`, `.env`. Ссылку `/usr/local/bin/mfagatectl`, образы и журналы установки не удаляет |

Команды инструмента внутри образа (`python -m mfa_gate …`, выполняются через `docker compose exec api`): `create-admin`, `emergency-access`, `create-api-client <имя> --scopes …`, `radius-test`, `diagnose`, `gen-master-key`, `migrate`.

## Переменные окружения

### Экземпляр (`deploy/.env`)

Читаются `mfagatectl` и `compose.yml`.

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `COMPOSE_PROJECT_NAME` | `mfa-gate` | имя экземпляра: префикс контейнеров, сетей, томов |
| `MFA_GATE_IMAGE` | `ghcr.io/calleograph/mfa-gate` | образ; имя без `/` (например, `mfa-gate`) — локальная сборка из `server/` |
| `MFA_GATE_VERSION` | из `VERSION` | зафиксированная версия образа (не `latest`) |
| `MFA_GATE_REPO` | `calleograph/mfa-gate` | репозиторий для `check-update` |
| `MFA_GATE_DATA`, `MFA_GATE_BACKUPS` | `./data`, `./backups` (относительно `deploy/`) | каталоги данных и копий |
| `MFA_GATE_HTTP_BIND`, `MFA_GATE_HTTP_PORT` | `0.0.0.0`, `8080` | публикация HTTP. За reverse proxy — `127.0.0.1` |
| `MFA_GATE_RADIUS_BIND`, `MFA_GATE_RADIUS_PORT` | `0.0.0.0`, `1812` | публикация RADIUS |
| `POSTGRES_PASSWORD` | генерируется | пароль базы |
| `MFA_GATE_NO_BUILD` | — | `1` — не собирать образ из исходников, если загрузка не удалась |
| `MFA_GATE_ENV_FILE` | `deploy/.env` | (окружение `mfagatectl`) другой файл конфигурации |

### Приложение

Источник — `server/mfa_gate/config.py` (префикс `MFA_GATE_`). Все переменные задаются в `deploy/.env` (пример со значениями по умолчанию — `deploy/.env.example`).

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `MFA_GATE_PUBLIC_URL` | — | внутренний адрес личного кабинета (`/`) и панели (`/admin/`); попадает в QR привязки, если не задан `MFA_GATE_DEVICE_URL` |
| `MFA_GATE_DEVICE_URL` | = `MFA_GATE_PUBLIC_URL` | внешний HTTPS-адрес Device API для телефонов (в QR привязки). Публикуется через reverse proxy только с префиксом `/api/device/`; если хост отличается от хоста `MFA_GATE_PUBLIC_URL`, сервер отвечает 404 на запросы к этому хосту вне `/api/device/` и `/health/` ([network.md](network.md)) |
| `MFA_GATE_PORTAL_ENROLL_NETWORKS` | `10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7` | сети, из которых разрешена первая регистрация фактора в кабинете по одному паролю; пусто — любой адрес (не рекомендуется) |
| `MFA_GATE_PORTAL_ENROLL_GROUPS` | пусто | группы MFA Gate или каталога, которым разрешена самостоятельная регистрация; пусто — все, кто прошёл первый фактор |
| `MFA_GATE_SERVER_NAME` | `MFA Gate` | имя в приложении и в записи TOTP |
| `MFA_GATE_PUSH_TIMEOUT` | `45` | секунд на ответ на push; таймаут RADIUS на шлюзе должен быть больше (≥ 60) |
| `MFA_GATE_TRUSTED_PROXIES` | `auto` | от каких адресов принимать `X-Forwarded-For` и `X-Forwarded-Proto`. `auto` — loopback и шлюз сети контейнера (reverse proxy на этом же хосте). Прокси на другом сервере: `auto,198.51.100.30/32`. Целые сети не указывайте |
| `MFA_GATE_COOKIE_SECURE` | `auto` | `auto` — флаг `Secure` у cookie панели, если запрос пришёл по HTTPS (в том числе через доверенный прокси); `true` / `false` — принудительно |
| `MFA_GATE_LOG_LEVEL` | `INFO` | уровень журнала |
| `MFA_GATE_RADIUS_REQUIRE_MESSAGE_AUTHENTICATOR` | `true` | отбрасывать Access-Request без Message-Authenticator. `false` ослабляет защиту от BlastRADIUS |
| `MFA_GATE_PUSH_FCM_CREDENTIALS_FILE` | пусто | путь к JSON FCM внутри контейнера ([push.md](push.md)) |
| `MFA_GATE_PUSH_UNIFIEDPUSH_HOSTS` | пусто | разрешённые хосты UnifiedPush (экспериментально) |
| `MFA_GATE_MASTER_KEY_FILE` / `MFA_GATE_MASTER_KEY` | задаёт compose | мастер-ключ (файл или значение) |
| `MFA_GATE_DATABASE_URL` | задаёт compose | строка подключения к PostgreSQL |
| `MFA_GATE_OTP_CHALLENGE_TIMEOUT` | `120` | секунд на ввод кода после Access-Challenge |
| `MFA_GATE_ENROLLMENT_TTL_HOURS` | `24` | срок QR-кода привязки телефона |
| `MFA_GATE_ADMIN_SESSION_MINUTES` | `30` | сессия панели (продлевается при работе) |
| `MFA_GATE_LOCKOUT_THRESHOLD`, `MFA_GATE_LOCKOUT_MINUTES` | `8`, `15` | блокировка после ошибок входа |
| `MFA_GATE_PUSH_HMS_APP_ID`, `MFA_GATE_PUSH_HMS_CLIENT_SECRET` | пусто | Huawei Push Kit (экспериментально) |
| `MFA_GATE_AUDIT_RETENTION_DAYS` | `365` | срок хранения журнала аудита |
| `MFA_GATE_AUTH_REQUEST_RETENTION_DAYS` | `180` | срок хранения истории входов |
| `MFA_GATE_DOCS_ENABLED` | `true` | `/api/docs` и `/api/openapi.json` |

Изменения `.env` применяются командой `sudo mfagatectl start`.

## HTTPS

MFA Gate не завершает TLS сам: кабинет, панель, REST API и Device API публикуются через reverse proxy с TLS. После первичной установки панель работает по `http://IP:порт/admin/` — только для изолированной сети. Пошагово: [HTTPS через reverse proxy](../wiki/HTTPS-через-reverse-proxy.md); примеры для двух имён (внутреннее — всё приложение, внешнее — только Device API): [deploy/proxy/nginx.conf](../deploy/proxy/nginx.conf), [deploy/proxy/Caddyfile](../deploy/proxy/Caddyfile); сеть целиком — [network.md](network.md).

Требования к прокси:

- перезаписывать `X-Forwarded-For` адресом клиента (MFA Gate берёт **последнее** значение и только от адресов из `MFA_GATE_TRUSTED_PROXIES`);
- передавать `X-Forwarded-Proto: https`;
- передавать исходный `Host`;
- таймаут чтения не меньше 60 секунд (long-poll телефонов до 30 с), буферизацию ответов выключить;
- в `.env`: `MFA_GATE_PUBLIC_URL=https://<имя>`, при внешней публикации Device API — `MFA_GATE_DEVICE_URL=https://<внешнее имя>`, `MFA_GATE_HTTP_BIND=127.0.0.1`; применить — `sudo mfagatectl start`.

Телефоны, привязанные по HTTP или до смены `MFA_GATE_DEVICE_URL`, нужно привязать заново.

## Межсетевой экран

Порты, опубликованные Docker, обходят `ufw` и цепочку `INPUT`. Фильтруйте в цепочке `DOCKER-USER` по исходному порту назначения (`--ctorigdstport`):

```bash
# RADIUS только от шлюза 198.51.100.1 (правило -I вставляется выше, поэтому сначала DROP, затем ACCEPT)
iptables -I DOCKER-USER -p udp -m conntrack --ctorigdstport 1812 --ctdir ORIGINAL -j DROP
iptables -I DOCKER-USER -p udp -m conntrack --ctorigdstport 1812 --ctdir ORIGINAL -s 198.51.100.1 -j ACCEPT
# сохранить: iptables-persistent (netfilter-persistent save)
```

Для панели по HTTP — то же с `-p tcp` и портом 8080. Другой вариант — указать в `MFA_GATE_RADIUS_BIND` / `MFA_GATE_HTTP_BIND` адрес конкретного интерфейса вместо `0.0.0.0`. Если порты нестандартные, подставьте их. При nftables-бэкенде и других настройках Docker правила могут отличаться — проверьте на своём хосте. MFA Gate в любом случае молча отбрасывает RADIUS-пакеты от адресов, не заведённых как RADIUS-клиенты.

## Несколько экземпляров и соседние проекты

Экземпляры не конфликтуют, если у каждого свои:

- каталог клона (данные и копии лежат внутри `deploy/`) или свои `MFA_GATE_DATA` и `MFA_GATE_BACKUPS`;
- `COMPOSE_PROJECT_NAME` (`mfa-gate`, `mfa-gate-test` …);
- свободные `MFA_GATE_HTTP_PORT` и `MFA_GATE_RADIUS_PORT` (`install` откажется запускаться на занятом порту).

```bash
sudo COMPOSE_PROJECT_NAME=mfa-gate-test MFA_GATE_HTTP_PORT=18481 MFA_GATE_RADIUS_PORT=11812 \
  MFA_GATE_PUBLIC_URL=https://mfa-test.example.org ./install.sh
```

Ссылка `/usr/local/bin/mfagatectl` указывает на последний установленный экземпляр; для остальных запускайте `<каталог>/deploy/mfagatectl`.

## Ручная установка

Для ОС, которые не поддерживает `install.sh`. Нужны Linux x86_64/aarch64, Docker Engine 24+ с Docker Compose v2 (`docker compose version`), `openssl`, `curl`, `ss` (iproute2).

```bash
git clone https://github.com/calleograph/mfa-gate.git /opt/mfa-gate
cd /opt/mfa-gate/deploy
sudo MFA_GATE_PUBLIC_URL=http://192.0.2.10:8080 ./mfagatectl install
sudo ./mfagatectl admin-init
```

**Ожидаемый результат:** `install` завершается строкой `готово: панель http://0.0.0.0:8080 …`, `admin-init` печатает временный пароль `admin`.

Без `MFA_GATE_PUBLIC_URL` первый `install` только создаёт `deploy/.env` из примера и останавливается: проверьте `MFA_GATE_PUBLIC_URL`, порты и образ, затем повторите `install`. Ссылку для удобства создайте сами: `sudo ln -s /opt/mfa-gate/deploy/mfagatectl /usr/local/bin/mfagatectl`. Дальше — как после `install.sh`: [Первоначальная настройка](../wiki/Первоначальная-настройка.md).
