# Исследование: эксплуатационная архитектура эталонный проект как образец для MFA Gate

Источник: `<эталонный проект>` (ветка `main`, версия 0.76.3, коммит «Релиз 0.76.3»). Репозиторий только читался, контейнеры не запускались.
Все пути ниже даны относительно корня этого репозитория. Адрес публичного репозитория проекта, домены и IP заменены на `<скрыто>`.

---

## 1. Структура Compose

Файл: `deployment/compose.yml` (один файл на экземпляр; GPU-надстройка `deployment/compose.gpu.yml`). Справочник проверенных версий: `deployment/compat.env` (информационный, установщик при расхождении только предупреждает).

**Как вызывается.** Compose никогда не запускают руками: все скрипты идут через функцию `dc()` из `scripts/lib/common.sh`:
`docker compose -p "$COMPOSE_PROJECT_NAME" --project-directory deployment --env-file .env -f deployment/compose.yml [--profile sip|video|lowlat]`.
Профили добавляются, только если компонент включён в `.env` и его образ есть (`compose_args()`).

**Имя проекта.** `name: ${COMPOSE_PROJECT_NAME:?COMPOSE_PROJECT_NAME is required}`. `container_name`, имена сетей и volumes не фиксируются, compose строит их из имени проекта. Поэтому на одном хосте уживаются два экземпляра и чужие проекты. `setup.sh` (`v_name`) отказывает, если на хосте уже есть контейнеры `<имя>-…`.

**Сервисы и ответственность**

| Сервис | Образ | Роль | Healthcheck | depends_on |
|---|---|---|---|---|
| `postgres` | `postgres:16-alpine@sha256:…` | БД; `max_connections` из `.env` | `pg_isready` | — |
| `redis` | `redis:7-alpine@sha256:…` | сессии, очереди, блокировки; `--requirepass`, AOF | `redis-cli ping` | — |
| `livekit` | собирается локально (`deployment/livekit`) | медиасервер | `wget 127.0.0.1:7880` | redis healthy |
| `backend` | собирается из `backend/` → `${COMPOSE_PROJECT_NAME}-backend:${IMAGE_TAG}` | FastAPI | python-urllib на `/api/v1/health/db` | postgres, redis, livekit healthy |
| `asr` | собирается из `asr-service/` | распознавание речи | `/healthz` (`start_period: 120s`) | redis, livekit |
| `web` | собирается из `frontend/` (nginx внутри) | статика и прокси к backend | `/healthz` | backend healthy |
| `livekit-sip`, `livekit-egress`, `mediamtx`, `live-relay` | `profiles: [sip]/[video]/[lowlat]` | необязательные компоненты, по умолчанию выключены | свои | — |

Для MFA Gate важно: **отдельного сервиса-мигратора нет.** Миграции запускают скрипты разовым контейнером `run --rm` (см. раздел 3).

**Общие якоря.** `x-logging: &default-logging` задаёт `json-file`, `max-size: 10m`, `max-file: 5` на всех сервисах. `x-app-env: &app-env` содержит общие переменные (POSTGRES_*, REDIS_*, LOG_LEVEL/LOG_FORMAT=json).

**Порты.** Порты хоста берутся только из переменных без значения по умолчанию: `${WEB_PORT:?}`, `${LIVEKIT_HTTP_PORT:?}`. Если переменная не задана, compose отказывается стартовать и не занимает порт молча. Привязка к адресу тоже задаётся переменной:
`"${WEB_BIND_ADDR:?}:${WEB_PORT:?}:8080/tcp"`, а служебный HTTP LiveKit жёстко привязан к `127.0.0.1:${LIVEKIT_HTTP_PORT:?}`.
PostgreSQL, Redis, ASR и mediamtx не публикуются, они доступны только во внутренней сети. `setup.sh` подбирает свободные порты сам (`find_port` проверяет и `ss`, и `docker ps`).

**Сети.** Одна сеть `default` на проект (`networks: default: {}`). Внешний вход идёт так: хостовый nginx (свой site-файл с маркером `# managed-by: reference:<проект>`) → `127.0.0.1:WEB_PORT` → контейнер `web` → `backend:8000`. Блок `location /internal/ { return 404; }` в `frontend/nginx.conf` закрывает внутренний API снаружи, и smoke-test это проверяет.

**Volumes.** Именованных volumes нет, используются bind-mount в `${DATA_ROOT:?}`: `postgres/`, `redis/`, `ca/`, `recordings/`, `exports/`, `updater/`, `ext/` (с `propagation: rslave`). Код (git checkout в `/opt/...`) и данные (`/srv/...-data`) разделены. Прежний CA-файл монтируется так: `${LDAP_CA_FILE:-/dev/null}:/run/secrets/ldap-ca.pem:ro`.

**Безопасность контейнеров.** `security_opt: no-new-privileges:true`, backend работает от uid 10001 (`backend/Dockerfile`, `USER appuser`), у `live-relay` задано `cap_drop: [ALL]`. Приоритет CPU настроен через `cpu_shares` (это вес, а не лимит). У LiveKit `ulimits.nofile`.

**Закрепление образов.** Сторонние образы закреплены тегом и digest (`postgres:16-alpine@sha256:…`). Базовый образ backend тоже: `ARG PYTHON_IMAGE=python:3.12-slim@sha256:…`. Python-зависимости точно фиксирует `backend/constraints.txt` (`pip install -r requirements.txt -c constraints.txt`).

**Совместное размещение.** `DEPLOYMENT.md` §1 и §7 описывают два профиля, `INSTALL_PROFILE=shared-host|standalone`. В shared-host установщик не ставит Docker и nginx, не меняет sysctl и файрвол, не выполняет `apt upgrade`/`prune`/`reboot` и не трогает чужие site-файлы. На хосте создаётся только свой site-файл, симлинк и `DATA_ROOT`. Список всего, что создаётся, приведён в `DEPLOYMENT.md` §7.1.

---

## 2. Установка

Цепочка вызовов: `install.sh` (корень, bootstrap) → `scripts/prereq.sh --install` → `scripts/setup.sh --auto` (мастер, `.env`) → `scripts/install.sh --profile …` (этапы) → `scripts/smoke-test.sh` → `scripts/bootstrap-admin.sh`.

**`install.sh` (корень, 153 строки)**
1. Перезапускает себя через `sudo`, если запущен не от root. Проверяет ОС (apt), CPU, RAM, диск и только предупреждает.
2. Определяет, есть ли уже установка: признак — файл `$DATA_ROOT/state/install-complete`, путь читается из `.env`. Если файл `install-pending` остался, это прерванная установка, и скрипт её продолжает. Если установка завершена, печатает команды update/status/admin-reset и выходит.
3. Ставит недостающие `git curl openssl` (без `upgrade`).
4. Версия по умолчанию — последний тег `vX.Y.Z` из `git ls-remote --tags --sort=-v:refname`, иначе `main`. Затем `git clone --depth 1 --branch "$REF"`.
5. Запускает `prereq.sh --install --profile standalone` (Docker Engine, Compose, Buildx, nginx), затем `setup.sh --auto`.

**`scripts/setup.sh` (мастер `.env`)**
- Копирует `.env.example` в `.env` с `chmod 600`. Значения записывает функция `set_var` через `dotenv_quote`: атомарная замена через временный файл, без `eval`.
- Генерирует секреты: `POSTGRES_PASSWORD`, `REDIS_PASSWORD` (`openssl rand -hex 24`), `LIVEKIT_API_SECRET`, `INTERNAL_API_TOKEN` (`rand -hex 32`), `APP_MASTER_KEY` (`openssl rand -base64 32`, то есть ровно 32 байта для AES-256-GCM).
- Самопроверка: каждый ключ читается обратно и через `bash source`, и через `load_env`, значение должно совпасть с записанным.
- Печатает предупреждение «сохраните копию `.env`/APP_MASTER_KEY отдельно».
- В режиме `--auto` выпускает самоподписанный TLS-сертификат в `$DATA/tls` (`chmod 700/600`) и ставит `NGINX_MANAGE=yes`. LDAP в `.env` при этом не заполняется, каталог настраивается в веб-интерфейсе.
- Дальше идут `preflight.sh` (только чтение), план `install.sh --dry-run`, подтверждение, `install.sh`, `smoke-test.sh`, `bootstrap-admin.sh`. После этого `install-pending` заменяется на `install-complete`.

**`scripts/install.sh` (этапы с возобновлением)**
Этапы: `prerequisites → preflight → dirs → kernel → models → pull → build → database → migrations → services → healthcheck → nginx → firewall → verify → helper → report`.
- Каждый этап печатает `[ok]/[warn]/[FAIL]`. Сбой выводится одной строкой `FAIL stage=… subsystem=…`, при этом `subsystem` отделяет проблему хоста или Docker от проблемы проекта. Повторить можно с `--from ЭТАП`, режим `--dry-run` только печатает план.
- `dirs`: `mkdir` каталогов в `DATA_ROOT`, `chmod 750 $DATA_ROOT`, `chown 10001:10001` только на каталоги, куда пишет контейнер. `updater/` получает `chmod 1777` (sticky), это канал обмена с хостовым помощником.
- `database`: `up -d --no-build postgres redis`, затем ожидание healthy.
- `migrations`: `dc run --rm --no-deps -T backend alembic upgrade head`, затем **обязательная сверка** `alembic current == alembic heads` (`alembic_verify` в `scripts/lib/verifylib.sh`). Код возврата 0 доказательством не считается.
- `healthcheck`: `status.sh --wait 240`. Если сервисы не поднялись, останавливаются только прикладные контейнеры, данные не трогаются.
- `nginx` выполняется **последним**: запись site-файла → `nginx -t` → `reload`. Если `nginx -t` не прошёл, скрипт откатывает только свои изменения, и `reload` не выполняется.
- В конце в `$DATA_ROOT/state/install-history.log` пишется строка с commit и списком этапов.

**Первый администратор.** `scripts/bootstrap-admin.sh` вызывает `python -m app.cli bootstrap-admin` внутри backend (`backend/app/cli.py`, `backend/app/services/local_admin.py`). Пароль генерируется случайно: 4×6 символов из алфавита без похожих знаков, около 137 бит. Он выводится один раз и только в терминал, в файлах и журналах его нет. В БД хранится хэш argon2id и флаг `must_change_password=True`. Если администратор уже есть, команда печатает `EXISTS=1` и пароль не показывает. Восстановление доступа: `scripts/admin-reset.sh` выдаёт новый пароль, закрывает все сессии, снимает блокировку и пишет событие в аудит с actor `user@host`.

---

## 3. Обновление, пересборка, миграции

**`scripts/update.sh` (521 строка, две фазы).** Весь код обёрнут в функции, потому что `git merge` может заменить файл скрипта на диске прямо во время работы.
- Фаза 1 (старый код): `git fetch` (с автопочинкой), выбор цели (upstream ветки или новейший релизный тег), сохранение локальных правок (патч, архив, `git stash` в `BACKUP_DIR/local-changes-*`), сравнение `.env` с новым `.env.example` **только по именам**. Затем `cp .env .env.backup-<время>` (600), запись состояния в `state/last-update.state` (`previous_git_commit`, `target_git_commit`), сохранение работающих образов под тегом `prev-<sha12>` для отката без пересборки. Потом `git merge --ff-only` или `checkout --detach <tag>`, и `exec` новой версии скрипта с `--phase2`.
- Фаза 2 (новый код): дописывает в `.env` безопасные новые параметры (существующие значения не меняются), закрепляет версию LiveKit и чистит кэш сборки при нехватке места. Дальше идут `preflight`, `build_images` (повторы при сетевых ошибках, переход BuildKit → legacy builder) и **резервная копия БД**. Если нужны миграции, приложение сначала останавливается (`upd_app_stop`), чтобы копия была точной. После этого выполняются `alembic upgrade head`, сверка current == head, `up -d --no-build`, ожидание healthy, проверка nginx-site, `verify`, сверка digest образов с релизом (`deps_postcheck`) и `smoke-test`.
- При `--no-backup` с ожидающими миграциями скрипт **останавливается до** изменения схемы.
- Автовосстановление (`upd_autorecover`):
  - сбой до перезапуска: код возвращается на прежний commit и прежний `.env`;
  - схема уже изменена: БД восстанавливается из копии, снятой при остановленном приложении (`upd_db_restore_exact`);
  - сервисы новой версии не поднялись: вызывается `rollback.sh --to prev`.
- Итог состоит из четырёх независимых статусов: `Update / Deployment / Health / Integrations`. Сбой LDAP (внешней интеграции) не делает обновление неудачным (код 3). Отчёт пишется в `$DATA_ROOT/updater/report.txt`, журнал — в `state/deploy-history.log`.

**Кто запускает миграции.** Только скрипты (`install.sh`, `update.sh`), разовым контейнером `docker compose run --rm --no-deps backend alembic upgrade head`. Backend при старте схему не мигрирует. Alembic асинхронный (`backend/migrations/env.py`, `create_async_engine`), URL берётся из `Settings().sqlalchemy_url`. Ревизии лежат в `backend/migrations/versions/0001_…` и далее, всего 66 файлов, нумерация последовательная.

**Теги образов.** `IMAGE_TAG = APP_GIT_COMMIT` (короткий SHA). `APP_VERSION`, `APP_GIT_COMMIT` и `APP_BUILT_AT` запекаются в образ через `ARG` в конце Dockerfile, чтобы не сбрасывать кэш слоёв, и **не** переопределяются окружением при запуске. `build_needed()` в `scripts/lib/dockerlib.sh` пересобирает образ только при изменении отпечатка исходников (`src_fingerprint`).

**Пересборка и пересоздание отдельных компонентов.**
- `scripts/rebuild.sh [backend|asr|web|livekit] [--if-changed] [--no-restart]` собирает тем же способом, что и update (с build-args версии), затем `dc up -d --no-build <svc>`, ожидание healthy и `verify`.
- `scripts/ctl.sh restart <svc>` выполняет `dc up -d --no-build --force-recreate --no-deps <svc>`. Обычный `docker compose restart` не перечитывает `.env`.

**Обновление из веб-интерфейса.** `scripts/updater.sh` — хостовая служба systemd `reference-updater-<проект>` от root. Контейнер кладёт `request.txt` (строки `key=value`) в `$DATA_ROOT/updater`. Служба выполняет только действия из белого списка (`check`, `update`, `repair <ID>`, `component`, `volume`) в стерильном окружении `env -i`. Временные файлы получают случайные имена, лог создаётся через `O_EXCL`: это защита от подмены symlink в каталоге с правами 1777. У backend доступа к Docker и git нет.

---

## 4. Бэкап, восстановление, откат

| Команда | Что делает |
|---|---|
| `scripts/backup.sh [--keep N] [--with-env] [--with-recordings] [--full] [--label X]` | `pg_dump -Fc` через `dc exec postgres` пишет в `${BACKUP_DIR}/<проект>-db-<YYYYmmdd-HHMMSS>[-label].dump`. Запись идёт в `.part`, затем `mv`. Проверка: `pg_restore --list`, при ошибке файл получает суффикс `.bad`. Рядом пишется `.sha256`. `--with-env` добавляет копию `.env` (600). `--with-recordings` добавляет `tar.gz` каталогов файлов с проверкой `gzip -t`. `BACKUP_COPY_DIR` задаёт копию набора на второй путь. Ротация по шаблону имени удаляет набор целиком, по умолчанию хранятся 14. `umask 077`, каталог `chmod 700`. |
| `scripts/restore.sh DUMP [--files ARCH] [--yes]` | Требует ввести имя проекта. Вызывает `restore_preflight` (место на диске, `pg_restore --list`) и делает страховочный `backup.sh --label pre-restore`. Затем останавливает `web backend asr`. Функция `db_restore_swap` из `scripts/lib/updtxn.sh` восстанавливает копию **в отдельную БД**, проверяет её и меняет базы именами (`ALTER DATABASE … RENAME`). Прежняя база сохраняется как `<db>_before_restore_<ts>`. Если приложение не стало healthy на той же версии схемы, прежняя база автоматически возвращается. Файлы подменяются целыми каталогами, старые остаются как `<dir>.before-restore-<ts>`. |
| `scripts/rollback.sh [--to SHA]` | Откатывает **код и образы**, но не схему БД. Сначала проверяет, что текущая ревизия Alembic известна целевому коду (`upd_rev_in_commit`). Если нет, откат останавливается (код 3) с инструкцией: восстановить дамп `pre-<sha>` или обновиться вперёд. Образы берутся по тегу `<sha12>` или `prev-<sha12>`, пересборки нет. Затем `checkout --detach`, `up -d --no-build`, ожидание healthy и запись в `deploy-history.log`. |

Без `APP_MASTER_KEY` дамп бесполезен, потому что зашифрованные настройки не прочитать. Поэтому полный набор (`--full`) включает `.env`. В приложении есть ещё шифрованный экспорт настроек: `backend/app/services/config_backup/container.py` (AES-256-GCM, ключ через scrypt от одноразового пароля, заголовок входит в AAD, пределы на размер и параметры scrypt) и автокопия на SMB (`services/auto_backup.py`).

---

## 5. Диагностика и статус

- **`/api/v1/health/live`** (`backend/app/api/health.py`) — процесс жив, зависимости не проверяются.
- **`/api/v1/health/db`** — PostgreSQL и Redis, ответ 200 или 503. Именно его использует healthcheck контейнера. PostgreSQL проверяется через **отдельный крошечный пул** (`create_health_engine`), поэтому исчерпание основного пула не маскируется под «база жива». Возможный статус — `degraded`, если пул занят на 90 % и больше.
- **`/api/v1/health/ready`** — обязательные зависимости (postgres, redis, livekit) дают 503 при сбое, необязательная (ASR) даёт `degraded` с кодом 200. Результат кэшируется на 2 с под `asyncio.Lock`, чтобы массовый опрос не создавал шторм проверок.
- **`/api/v1/version`** — `{version, commit, built_at}`.
- **`/internal/v1/diag?deep=1`** — глубокий отчёт (`services/diagnostics.py`, LDAP, БД, Redis, ASR). Доступ по `Authorization: Bearer INTERNAL_API_TOKEN` со сравнением через `hmac.compare_digest` (`auth/deps.py`). Снаружи этот путь отдаёт 404.
- **`scripts/status.sh [--wait N]`** — `dc ps`, `State.Status` и `Health.Status` каждого сервиса, версия backend, последние 3 деплоя. Код 0 означает, что все сервисы running/healthy.
- **`scripts/verify.sh`** — `verify_deployment` из `scripts/lib/verifylib.sh`: контейнеры, БД, Redis, Alembic current == head, HTTP-цепочка через nginx, порты, sysctl. Каждый этап получает отдельный итог PASS/FAIL.
- **`scripts/smoke-test.sh [--login USER]`** — сквозная проверка с итоговой таблицей. Проверяет: frontend 200; backend live/ready; что `/internal/` даёт 404 снаружи; `pg_isready`; Redis `PONG`; Alembic; LDAP (bind сервисной учётки через `/internal/v1/diag`); ASR с тестовым инференсом; LiveKit; TLS публичного URL без `-k`; тестовую сессию. С `--login` выполняет настоящий вход: пароль вводится без эха, затем `/auth/me` и logout. **Коды возврата:** 0 — всё в порядке; 1 — сбой самой системы; 3 — сбой внешнего окружения (LDAP, прокси, сертификат), выводится строкой `Integration issues: …`.
- **`scripts/diag.sh [--out DIR] [--tail N] [--no-public]`** — архив `reference-diag-<ts>.tar.gz` (600) плюс `.sha256`. Состав: `summary.txt`, `containers.txt`, `compose.config.masked.yml`, `env.masked`, `logs/<svc>.log` (хвосты), `host.txt`, `resources.txt` (память, OOM, диск, inode, `docker stats`, перезапуски), `network.txt` (API локально и публично, DNS, цепочка TLS), `updates.txt`, `backend-diag.json`, `README.txt`. Всё проходит через `mask_stream` (`scripts/lib/mask.sh`): маскируются ключи `*PASSWORD|SECRET|TOKEN|KEY*`, JWT, `user:pass@` в URL, `Authorization`, JSON-пары. **Финальная проверка:** каждое значение секрета из `.env` (`env_secret_values`) ищется в пакете через `grep -F`, и при находке архив не создаётся (код 2).
- **`scripts/repair.sh --scan | <ID>`** — исправления только из белого списка `REPAIR_IDS` (`scripts/lib/repairlib.sh`).

---

## 6. LDAP / AD

**Файлы**
- `backend/app/auth/directory.py` — `LdapConfig`, `LdapDirectory` (ldap3), `DirectoryClient` (Protocol для подмены в тестах), `parse_login`, `classify_bind_failure`, `diagnose_ldap`.
- `backend/app/services/ldap_profiles.py` — `LdapService` (CRUD подключений в таблице `LdapProfile`, шифрование пароля) и `ProfileDirectory` (составной каталог из нескольких подключений, отказоустойчивость).
- `backend/app/services/ca_bundle.py` — управляемый набор CA, который загружается через UI. Сборка в `<DATA_DIR>/ca/bundle.pem`. Закрытые ключи отклоняются по регулярному выражению `PRIVATE KEY|PKCS12|…`. Перед сохранением показываются Subject, Issuer, SHA-256 и срок действия.
- `backend/app/auth/service.py::access_decision` — роли по группам.
- `backend/app/config.py` — прежний способ через `.env` (`LDAP_URIS` и др.). Валидатор `_only_ldaps` запрещает схему `ldap://`.

**LDAPS и сертификат.** Проверка обязательна, отключить её нельзя: без CA-файла `LdapDirectory.__init__` выбрасывает `config_error`. Поддерживаются протоколы `ldaps` (636) и `starttls` (389). Нешифрованный LDAP отклоняется в `_clean`.
```python
tls = Tls(validate=ssl.CERT_REQUIRED, version=ssl.PROTOCOL_TLS_CLIENT, ca_certs_file=self._s.ldap_ca_file)
Server(host, port=port or (389 if starttls else 636), use_ssl=not starttls, tls=tls,
       connect_timeout=self._s.ldap_timeout_seconds, get_info=None)
ServerPool(servers, ROUND_ROBIN, active=True, exhaust=True)
Connection(pool, user=..., password=..., authentication=SIMPLE, auto_bind=False,
           raise_exceptions=False, read_only=True, receive_timeout=self._s.ldap_timeout_seconds)
```
Имя хоста проверяется за счёт `PROTOCOL_TLS_CLIENT`, у которого `check_hostname` включён по умолчанию.

**Bind сервисной УЗ.** `_service_connection()`. Ошибки сервисной учётки не выдаются за ошибку пароля пользователя: они превращаются в `service_account_error`. `server_unavailable` и `tls_error` пробрасываются как есть.

**Аутентификация пользователя** (`authenticate`):
1. Пустой пароль отклоняется, потому что пустой пароль означает анонимный bind.
2. `parse_login` принимает `user`, `DOMAIN\user` и `user@domain` (UPN, поиск по `userPrincipalName`). Логин проходит по белому списку символов `^\w[\w.\-]{0,63}$`.
3. Поиск выполняется от сервисной учётки: `(&(objectCategory=person)(objectClass=user)(<attr>=escape_filter_chars(v)))` с `size_limit=2`. Ноль записей даёт `user_not_found`, больше одной — `ambiguous_user`.
4. Проверяются `objectGUID` (16 байт, перевод через `uuid.UUID(bytes_le=…)`) и `userAccountControl & 0x2` (учётка отключена).
5. Пароль проверяется bind от DN пользователя, сразу за ним `unbind`.
6. Группы определяются одним запросом с учётом вложенности. **Используется `LDAP_MATCHING_RULE_IN_CHAIN`:**
```python
gflt = f"(&(objectClass=group)(member:1.2.840.113556.1.4.1941:={escape_filter_chars(dn)}))"
svc.extend.standard.paged_search(base_dn, gflt, search_scope=SUBTREE,
                                 attributes=["distinguishedName"], paged_size=500, generator=True)
```
Результат — `frozenset` DN в нижнем регистре. Профильные атрибуты читаются по белому списку `title`, `department`, `telephoneNumber`.

**Классификация ошибок AD** (`classify_bind_failure`, переносить целиком): `data 775` → account_locked, `533/701` → account_disabled, `532` → password_expired, `773` → password_must_change, `52f/530/531` → account_restriction, иначе код 49 → invalid_credentials. Коды `50` → insufficient_access, `51/52/81` → server_unavailable. В `_transport_error` строки с `ssl/certificate/tls/hostname` превращаются в `tls_error`.

**Таймауты.** `connect_timeout` и `receive_timeout` равны `timeout_s` профиля: по умолчанию 5, допустимо от 1 до 60 (валидация в `_clean` и `Field(ge=1, le=60)`).

**Несколько DC и отказоустойчивость**
- Внутри одного `LdapConfig` используется `ServerPool(ROUND_ROBIN, active=True, exhaust=True)` по списку URI. Список URI задаётся через `.env LDAP_URIS`. В профилях из UI у каждого профиля **один** хост. Пул создаётся заново на каждое соединение (`_server_pool()` в `_connection`).
- Между профилями работает `ProfileDirectory.authenticate`. Профили перебираются по `position`. Кандидаты сужаются по NetBIOS-домену или UPN-суффиксу из логина. На `user_not_found` проверяется следующий профиль. На `server_unavailable/tls_error/service_account_error/config_error` ошибка запоминается и проверяется следующий. На неверный пароль или блокировку перебор **сразу останавливается**. Резервный DC оформляется вторым профилем.
- Синхронизация между процессами: `reload_if_changed` сравнивает подпись строк таблицы и перечитывает профили, иначе выключенное подключение продолжало бы пускать пользователей.
- Вызовы ldap3 блокирующие и выполняются в потоке.

**Пошаговая диагностика** (`diagnose_ldap(cfg)`): `config` (адрес и CA) → `dns` (`getaddrinfo`) → `tcp` (`create_connection` с таймаутом) → `tls` (`ssl.create_default_context(cafile=…)` + `wrap_socket(server_hostname=host)`, понятные причины: несовпадение имени/SAN, просрочка, недоверенная цепочка) → `certificate` (notAfter) → `bind` (сервисная учётка) → `base_dn` (BASE-поиск `(objectClass=*)`). Каждый шаг возвращает `{stage, ok, message, ms}` без секретов. Ограничения: проверяется только **первый** URI профиля, отдельного шага «группы» нет. Группы проверяются функцией `lookup(login)`, которая ищет пользователя и его группы без пароля и используется в админке для проверки правил доступа.

**Что стоит учесть.** `objectCategory=person` и правило 1.2.840.113556.1.4.1941 работают только в AD. Для OpenLDAP и FreeIPA нужен другой фильтр групп (например, `memberOf` или рекурсия в коде).

---

## 7. Секреты, пароли, роли

- **Секреты инфраструктуры** хранятся только в `.env` (600, вне git): пароли БД и Redis, ключи LiveKit, `INTERNAL_API_TOKEN`, `APP_MASTER_KEY`. Источник конфигурации backend — только переменные окружения (`Settings(BaseSettings)`, `env_file=None`). Compose передаёт их явным перечислением, `env_file` в compose не используется.
- **Секреты интеграций в БД** (пароль bind LDAP, SMTP, TOTP-секреты, SMB) шифруются в `backend/app/security/secretbox.py`. Алгоритм AES-256-GCM, формат `base64(version(1) | nonce(12) | ct+tag)`. Ключ `APP_MASTER_KEY` (base64 от 32 байт) в БД не хранится. **AAD привязан к записи:** `f"ldap_profile.{pid}"`, поэтому шифртекст нельзя переставить в другую строку. Пароль обратно в API не отдаётся, отдаётся только `secret_set: bool`. `None` в обновлении означает «не менять», пустая строка запрещена.
- **Пароли.** Пароли AD не хранятся нигде. Локальный (аварийный) администратор хранится как argon2id (`argon2-cffi`, `PasswordHasher()` с параметрами по умолчанию, `security/passwords.py`). Проверка выполняется в `asyncio.to_thread`, чтобы не блокировать цикл событий. Политика: длина не меньше 12, минимум 2 класса символов, пароль не должен содержать логин. При первом входе пароль нужно сменить.
- **Роли.** Признак администратора — пересечение групп пользователя (вложенных, из LDAP) с `admin_groups` из `.env LDAP_ADMIN_GROUP_DN` и настроек «access». Группы читаются при каждом входе. `access_groups` ограничивают допуск обычных пользователей. У профиля есть флаги `use_for_users` и `use_for_admins`. Локальный администратор всегда `is_admin=True` (`auth_source="local"`). Ролевая модель продукта описана в `services/roles.py`. Есть TOTP 2FA (`api/auth_totp.py`): политика `all/admins/groups`, резервные коды, защита от повтора (`totp_last_step`).
- **Сравнение токенов** выполняется через `hmac.compare_digest`. CSRF проверяется заголовком `X-CSRF-Token`. Проверка Origin выполняется по `APP_PUBLIC_URL` и `ALLOWED_ORIGINS` (`config.py::origin_allowed`).

---

## 8. SMTP (кратко)

`backend/app/services/mail.py`: профили в БД с зашифрованным паролем (та же схема с AAD). Режимы `none/starttls/ssl` (порты 25, 587, 465), аутентификация `none/login/ntlm`, `timeout=15`. Пошаговая проверка `check_connection` проходит `dns → connect → tls → auth_methods → auth`. Ошибки smtplib и ssl переводятся в понятные причины с флагом `transient`, отказ отправителя или получателя разбирается по коду. CA берётся из общего набора `ca_bundle`. **Отличие от LDAP:** у SMTP проверку сертификата можно отключить флагом `verify_cert`.

---

## 9. CI/CD и выпуск

- **`scripts/release.sh patch|minor|major|X.Y.Z`.** Версия задаётся в одном месте, файле `VERSION`. Скрипт проверяет чистоту дерева, проводит ревизию зависимостей (`scripts/deps.py verify --online --max-age 3`), переносит «## Unreleased» в «## X.Y.Z (дата)» в `CHANGELOG.md`, создаёт commit «Релиз X.Y.Z» и аннотированный тег `vX.Y.Z` с текстом раздела CHANGELOG. **`release.sh push`** отправляет ветку, ждёт зелёный CI этого коммита (`scripts/ci_wait.py`) и **только потом** пушит тег, потому что серверы обновляются по новейшему тегу. `release.sh check` проверяет согласованность VERSION, CHANGELOG, package.json и тега.
- **`.github/workflows/ci.yml`:**
  - `pytest` backend и ASR;
  - фронтенд: `npm ci`, typecheck, тесты, сборка;
  - `bash -n` и `shellcheck -S warning` всех скриптов;
  - тесты скриптов `tests/scripts/run.sh` и nginx;
  - `docker compose … config -q` на `.env.example`;
  - сборка образов так же, как при обновлении, плюс самопроверка образа ASR;
  - нагрузочный smoke;
  - матрица `clean-os`: `prereq.sh --install` на чистых образах ОС, повторный запуск безопасен, `install.sh --dry-run`;
  - `scripts/check_private_data.py`: сканер частных данных по списку разрешённого `scripts/private-data.allow`.
- **`.github/workflows/release.yml`** запускается по тегу `v*`. Задание `ci-gate` ждёт зелёный CI того же SHA через `gh run list`, затем проверяет, что тег совпадает с `VERSION`, и выполняет `gh release create --notes-file` с текстом из CHANGELOG.
- `deps-watch.yml` раз в неделю сверяет закреплённые версии с PyPI, npm, Docker Hub, ghcr и OSV. `android.yml` реагирует на отдельные теги `android-v*`.
- **GHCR для своих образов не используется.** Образы backend, web и asr собираются на сервере клиента из исходников по тегу. ghcr упоминается только как источник сторонних образов в `deps.py`.

---

## 10. Что берём в MFA Gate

1. **Compose-каркас** по образцу `deployment/compose.yml`: `name: ${COMPOSE_PROJECT_NAME:?}`, без `container_name`; порты только как `${BIND_ADDR:?}:${PORT:?}`; PostgreSQL не публикуется; якорь `x-logging` (json-file 10m×5); `security_opt: no-new-privileges`; uid 10001; `depends_on: condition: service_healthy`; bind-mount в `${DATA_ROOT:?}`; сторонние образы с `@sha256`. Для RADIUS порт `1812/udp` (и 1813) публикуется через `${RADIUS_BIND_ADDR:?}:${RADIUS_PORT:?}`, это единственный не-loopback порт.
2. **Разделение health:** `/health/live` (процесс) и `/health/db` (для healthcheck, отдельный маленький пул) по образцу `backend/app/api/health.py`, а также `/health/ready` с делением на обязательные и `degraded` зависимости и кэшем на 2 с. Для RADIUS-сервиса нужен отдельный healthcheck: локальный Status-Server или проверка сокета.
3. **Миграции разовым контейнером** `run --rm --no-deps <app> alembic upgrade head`, затем обязательная сверка `alembic current == heads` (`scripts/lib/verifylib.sh::alembic_verify`). Перед миграцией — `pg_dump -Fc` при остановленном приложении.
4. **Установщик этапами** (`scripts/install.sh`): `run_stage`, `--from`, `--dry-run`, `FAIL stage= subsystem=`, nginx последним с `nginx -t` и откатом своих правок, метки `state/install-pending|install-complete`.
5. **Мастер `.env`** (`scripts/setup.sh`): `set_var` + `dotenv_quote` + самопроверка чтения, генерация секретов через `openssl rand`, `chmod 600`, подбор свободных портов, предупреждение «сохраните копию мастер-ключа».
6. **Первый администратор** через CLI внутри контейнера (`app/cli.py bootstrap-admin|admin-reset`, `services/local_admin.py`): случайный пароль один раз в TTY, argon2id, `must_change_password`, аудит. Для MFA-продукта это аварийный вход в обход LDAP, поэтому его стоит сразу защитить вторым фактором.
7. **backup/restore/rollback:** `.part` → `mv`, `pg_restore --list`, `.sha256`, ротация по набору, `--with-env`. Восстановление в отдельную БД с обменом именами (`scripts/lib/updtxn.sh::db_restore_swap`, `restore_from_dump`). Rollback отказывает, если ревизия БД неизвестна целевому коду. Образы `prev-<sha>`.
8. **Двухфазный `update.sh`** с `exec` новой версии и автовосстановлением. Для первой версии MFA Gate достаточно упрощённого варианта: fetch → backup → build → migrate → up → verify → smoke, откат при неудаче.
9. **Диагностический архив** `scripts/diag.sh` + `scripts/lib/mask.sh`: маскирование, **проверка на утечку значений секретов из `.env` до упаковки**, `.sha256`. В MFA Gate секретами считаются также RADIUS shared secret и TOTP seed.
10. **smoke-test с кодами 0/1/3**: системный сбой отделяется от сбоя внешней интеграции (AD, NAS-клиенты). Для MFA Gate нужно добавить тестовый Access-Request на `127.0.0.1` с тестовой учёткой.
11. **LDAP-модуль почти целиком:** `auth/directory.py` (Tls CERT_REQUIRED, ServerPool, `parse_login`, `escape_filter_chars`, пустой пароль запрещён, `classify_bind_failure`, IN_CHAIN-группы, `diagnose_ldap`) и `services/ldap_profiles.py` (профили в БД, перебор по профилям, `reload_if_changed`), а также `services/ca_bundle.py` (загрузка CA без закрытых ключей). Добавить: диагностику всех URI, шаг `groups`, асинхронную обёртку с ограничением потоков.
12. **SecretBox** (`security/secretbox.py`) с AAD на запись для секретов в БД: пароль bind, SMTP, **секреты TOTP и RADIUS-клиентов**. Мастер-ключ хранится в `.env`.
13. **Выпуск:** `VERSION` как единый источник, `release.sh` (тег только после зелёного CI), `release.yml` с `ci-gate`, `check_private_data.py` по списку разрешённого, shellcheck и `compose config -q` в CI, матрица clean-os для установщика.
14. **Профили shared-host и standalone** и список того, что установщик никогда не делает (`DEPLOYMENT.md` §1, §7.1).

## 11. Что не берём и почему

- **Сборку образов на сервере клиента** (BuildKit/legacy fallback, `netcheck`, отпечатки исходников, `upd_disk_maintenance`). В эталонном проекте это источник большей части сложности `update.sh` и `dockerlib.sh`. Для MFA Gate проще и надёжнее публиковать подписанные образы в GHCR и закреплять их по digest, а серверу оставить только `pull`.
- **Хостовый root-помощник обновлений** (`scripts/updater.sh`, systemd, канал в каталоге 1777). Это большая поверхность атаки для продукта безопасности. Первую версию MFA Gate лучше обновлять только из консоли, а веб-кнопку рассматривать позже и отдельно.
- **Обновление через git checkout** на сервере (`git merge --ff-only`, автопочинка локальных правок, сохранение stash). При поставке готовых образов на сервере достаточно compose-файла и `.env`, git не нужен.
- **Медиа-специфику:** LiveKit, ASR, Egress, sysctl для UDP, `cpu_shares`, медиасеть и ICE, модели.
- **Прежний способ LDAP через `.env`** наряду с профилями в БД. Это груз совместимости. В MFA Gate нужен один источник: профили в БД, а в `.env` только мастер-ключ.
- **Отключаемую проверку сертификата SMTP** (`verify_cert=False`). В MFA-продукте TLS без проверки допускать не стоит, или только с явным предупреждением в аудите.
- **Режим `--with-env` в обычном бэкапе без шифрования.** Копия `.env` рядом с дампом лежит открытым текстом. В MFA Gate полный набор нужно шифровать, по образцу `config_backup/container.py` (AES-GCM + scrypt).
- **Отказ от отдельного мигратор-сервиса** сам по себе не плох, но MFA Gate не будет собирать образы на месте. Поэтому удобнее one-shot сервис `migrate` в compose (`profiles: [tools]`) или та же команда `run --rm` из скрипта; решение оставить за ADR.
