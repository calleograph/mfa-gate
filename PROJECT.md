# MFA Gate — текущее состояние

Версия 0.2.0. Рабочий каталог проекта — корень этого репозитория; прежняя реализация на Laravel (до 2026.06.15) упакована в локальный `_archive/` и в публикацию не входит.

## Назначение
Открытая (Apache-2.0) платформа многофакторной аутентификации для VPN и корпоративных ресурсов: RADIUS-сервер для VPN-шлюзов, push-подтверждение в собственном Android-приложении с криптографической подписью, TOTP, LDAP/Active Directory, политики доступа, REST API и веб-панель. Разворачивается в Docker Compose на произвольном Linux-сервере, в том числе рядом с другими проектами.

## Архитектура
- Один образ `mfa-gate` (Python 3.13, FastAPI, SQLAlchemy async, Alembic), роли: `migrate` → `api` (REST API v1, Device API, панель) + `radius` (UDP 1812) + `worker` (push, истечение запросов, очистка, пульс). PostgreSQL 16. Redis не используется: события — `LISTEN/NOTIFY`, очередь push — таблица с `SKIP LOCKED`.
- Сценарий входа (`server/mfa_gate/services/authflow.py`): блокировка → первый фактор (локальный argon2id или каталоги по приоритету с автосозданием) → политика (первое совпадение, иначе MFA) → push (удержание RADIUS-запроса до решения) или TOTP через Access-Challenge.
- Push: решение подписывается ключом ECDSA P-256 из Android Keystore (биометрия), push — только сигнал. Провайдеры FCM / HMS / UnifiedPush / long-poll.
- Решения зафиксированы в `docs/adr/` (13 ADR), модель угроз — `docs/threat-model.md`.

## Важные каталоги и файлы
- `server/mfa_gate/` — `config.py` (переменные MFA_GATE_*), `models.py`, `api/` (`auth.py` вход администратора и интеграционный API, `manage.py` управление, `device.py` Device API, `deps.py` права и аутентификация), `radius/` (`packet.py` кодек, `server.py`), `services/` (`authflow`, `policy`, `ldap`, `push`, `events`, `audit`, `health`), `security/` (`totp`, `secretbox`, `crypto`), `worker.py`, `cli.py`.
- `server/migrations/` — Alembic; `server/tests/` — юнит, LDAP (настоящий OpenLDAP), сквозные (`test_stack.py`).
- `server/web/` — веб-панель (ES-модули без сборки, строгая CSP).
- `install.sh` — установка All-in-One на Ubuntu/Debian одной командой (Docker, конфигурация, запуск, первый администратор).
- `deploy/compose.yml`, `deploy/.env.example`, `deploy/mfagatectl` — эксплуатация: обновление, откат, копии, восстановление, диагностика.
- `android/` — приложение MFA Gate Authenticator (Kotlin, Compose, flavors foss/gms).
- `scripts/e2e.sh` — изолированный стенд и сквозные проверки; `scripts/check_private_data.py` — сканер частных данных.
- `docs/` — протокол устройства, ADR, модель угроз, установка, руководства, интеграции, API, исследования.

## Технологии и зависимости
Сервер: fastapi, uvicorn, SQLAlchemy 2 + asyncpg, alembic, pydantic 2, ldap3 (LGPL, как библиотека), cryptography, argon2-cffi, httpx, segno. Android: AGP 9.5, Kotlin 2.4, Compose BOM, AndroidX Biometric, ZXing, OkHttp, kotlinx-serialization; Firebase — только flavor gms.

## Важные настройки
`MFA_GATE_PUBLIC_URL` (адрес для QR привязки), `MFA_GATE_PUSH_TIMEOUT` (45 с; таймаут шлюза должен быть больше), `MFA_GATE_RADIUS_REQUIRE_MESSAGE_AUTHENTICATOR` (true), `MFA_GATE_TRUSTED_PROXIES`, `MFA_GATE_PUSH_FCM_CREDENTIALS_FILE`, `MFA_GATE_PUSH_UNIFIEDPUSH_HOSTS`, мастер-ключ `data/secrets/master.key` (хранить копию отдельно).

## Реализовано
- Установка All-in-One (`install.sh`): проверка сервера, установка Docker, конфигурация, свободные порты, образы (GHCR или сборка из исходников), миграции, первый администратор с временным паролем и обязательной сменой, проверка API/панели/RADIUS, журнал установки без паролей.
- Работа по HTTP для первичной настройки (cookie `Secure` — по схеме запроса, в том числе `X-Forwarded-Proto` от доверенного прокси).
- Ограничитель частоты входа в PostgreSQL — общий для нескольких экземпляров api.
- RADIUS PAP + Message-Authenticator, Access-Challenge для TOTP, дедупликация повторов, присоединение повторной попытки к открытой транзакции, проверка NAS-Identifier.
- Push-подтверждение с подписью, привязка устройства по одноразовому QR, long-poll, отзыв устройства.
- TOTP (RFC 6238, окно ±1, защита от повтора), выпуск и подтверждение из панели.
- LDAP/AD: LDAPS, несколько контроллеров, вложенные группы, разрешённые группы, пошаговая диагностика.
- Пользователи (локальные и из каталога), группы с сопоставлением группам AD, ресурсы, RADIUS- и API-клиенты (scopes, CIDR, ротация), политики, история входов, «Проблемы входа», аудит с CSV, диагностика.
- Вход администратора: пароль + обязательный TOTP/push, первичная настройка TOTP, CSRF, роли superadmin/admin/operator/auditor, аварийный одноразовый код из консоли.
- REST API v1 с OpenAPI (`/api/docs`), интеграционные запросы доступа.
- mfagatectl: install, admin-init, uninstall, update (копия → образ → миграции → healthy → автооткат), rollback, backup/restore, diagnose, check-update.

## Известные ограничения
- Не проверено на реальном VPN-шлюзе (Ideco, OpenVPN, NPS) и реальном Android-устройстве; на сервере разработки нет эмулятора/KVM.
- FCM требует проекта Firebase организации; HMS и UnifiedPush — экспериментально, без проверки на реальных аккаунтах.
- Нет RADIUS Accounting, режима «пароль+код», атрибутов ответа по группам, number matching, лимита push на пользователя, счётчика блокировок по адресу.
- Нет портала самообслуживания пользователя, SMTP-приглашений, резервных кодов в API, импорта пользователей группой из каталога.

## Проверки
Юнит 57 + сквозные 22 (`server/tests`), LDAP на OpenLDAP 17, Docker-стенд 13/13 этапов, Android 28 JVM-тестов и сборка APK, панель в браузере на 7 ширинах — подробно в `docs/testing.md`.

## Текущие незавершённые задачи
- Проверка на реальном шлюзе и телефоне; публикация репозитория на GitHub (после проверки истории и сканера частных данных) и первого выпуска в GHCR.

