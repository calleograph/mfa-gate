# MFA Gate — текущее состояние

Версия 0.3.0. Рабочий каталог проекта — корень этого репозитория; прежняя реализация на Laravel (до 2026.06.15) упакована в локальный `_archive/` и в публикацию не входит.

## Назначение
Открытая (Apache-2.0) корпоративная платформа второго фактора. Три пространства одной системы: личный кабинет сотрудника (`/` — вход доменной учётной записью, самостоятельная настройка TOTP или телефона, резервные коды), панель управления (`/admin/`) и серверная часть авторизации (RADIUS для VPN-шлюзов, REST API, Device API для Android-приложения с подписанными решениями). LDAP/Active Directory, политики, журнал. Разворачивается в Docker Compose на произвольном Linux-сервере, в том числе рядом с другими проектами.

## Архитектура
- Один образ `mfa-gate` (Python 3.13, FastAPI, SQLAlchemy async, Alembic), роли: `migrate` → `api` (личный кабинет `/`, панель `/admin/`, REST API v1, Device API `/api/device/v1/`) + `radius` (UDP 1812) + `worker` (push, истечение запросов, очистка, пульс). PostgreSQL 16. Redis не используется: события — `LISTEN/NOTIFY`, очередь push — таблица с `SKIP LOCKED`.
- Сценарий входа (`server/mfa_gate/services/authflow.py`): блокировка → первый фактор (локальный argon2id или каталоги по приоритету с автосозданием) → политика (первое совпадение, иначе MFA) → push (удержание RADIUS-запроса до решения) или TOTP через Access-Challenge.
- Push: решение подписывается ключом ECDSA P-256 из Android Keystore (биометрия), push — только сигнал. Провайдеры FCM / HMS / UnifiedPush / long-poll.
- Сессии: одна таблица `admin_sessions` с видом `admin|portal`, у каждого вида свой cookie (`mfa_gate_session`, `mfa_gate_portal`).
- Публикация: внутренний адрес `MFA_GATE_PUBLIC_URL` (кабинет и панель), внешний `MFA_GATE_DEVICE_URL` только для `/api/device/` (сервер на этом хосте отдаёт только Device API). Карта адресов — `services/publication.py`, экран «Адреса и публикация».
- Решения зафиксированы в `docs/adr/` (17 ADR), модель угроз — `docs/threat-model.md`, схема для сетевиков — `docs/network.md`.

## Важные каталоги и файлы
- `server/mfa_gate/` — `config.py` (переменные MFA_GATE_*), `models.py`, `api/` (`auth.py` вход администратора и интеграционный API, `portal.py` личный кабинет, `manage.py` управление, `device.py` Device API, `deps.py` права, сессии и аутентификация), `radius/` (`packet.py` кодек, `server.py`), `services/` (`authflow`, `policy`, `ldap`, `push`, `events`, `audit`, `health`, `publication`), `security/` (`totp`, `secretbox`, `crypto`), `worker.py`, `cli.py`.
- `server/migrations/` — Alembic; `server/tests/` — юнит, LDAP (настоящий OpenLDAP), сквозные (`test_stack.py`).
- `server/web/` — `index.html` + `assets/js/portal.js` (личный кабинет), `admin.html` + `assets/js/app.js` (панель); ES-модули без сборки, строгая CSP, тема «Стекло».
- `install.sh` — установка All-in-One на Ubuntu/Debian одной командой (Docker, конфигурация, запуск, первый администратор).
- `deploy/compose.yml`, `deploy/.env.example`, `deploy/mfagatectl` — эксплуатация: обновление, откат, копии, восстановление, диагностика; `deploy/proxy/` — примеры nginx и Caddy с двумя адресами.
- `android/` — приложение MFA Gate Authenticator (Kotlin, Compose, flavors foss/gms).
- `scripts/e2e.sh` — изолированный стенд и сквозные проверки; `scripts/check_private_data.py` — сканер частных данных.
- `docs/` — протокол устройства, ADR, модель угроз, установка, руководства, интеграции, API, исследования.

## Технологии и зависимости
Сервер: fastapi, uvicorn, SQLAlchemy 2 + asyncpg, alembic, pydantic 2, ldap3 (LGPL, как библиотека), cryptography, argon2-cffi, httpx, segno. Android: AGP 9.5, Kotlin 2.4, Compose BOM, AndroidX Biometric, ZXing, OkHttp, kotlinx-serialization; Firebase — только flavor gms.

## Важные настройки
`MFA_GATE_PUBLIC_URL` (внутренний адрес кабинета и панели), `MFA_GATE_DEVICE_URL` (внешний адрес телефонов, в QR привязки), `MFA_GATE_PORTAL_ENROLL_NETWORKS` / `MFA_GATE_PORTAL_ENROLL_GROUPS` (кто может сам настроить первый фактор), `MFA_GATE_PUSH_TIMEOUT` (45 с; таймаут шлюза должен быть больше), `MFA_GATE_RADIUS_REQUIRE_MESSAGE_AUTHENTICATOR` (true), `MFA_GATE_TRUSTED_PROXIES`, `MFA_GATE_PUSH_FCM_CREDENTIALS_FILE`, `MFA_GATE_PUSH_UNIFIEDPUSH_HOSTS`, мастер-ключ `data/secrets/master.key` (хранить копию отдельно).

## Реализовано
- Установка All-in-One (`install.sh`): проверка сервера, установка Docker, конфигурация, свободные порты, образы (GHCR или сборка из исходников), миграции, первый администратор с временным паролем и обязательной сменой, проверка API/панели/RADIUS, журнал установки без паролей.
- Работа по HTTP для первичной настройки (cookie `Secure` — по схеме запроса, в том числе `X-Forwarded-Proto` от доверенного прокси).
- Ограничитель частоты входа в PostgreSQL — общий для нескольких экземпляров api.
- RADIUS PAP + Message-Authenticator, Access-Challenge для TOTP, дедупликация повторов, присоединение повторной попытки к открытой транзакции, проверка NAS-Identifier; источник первого фактора на клиенте (`mfa_gate` | `gateway` — профиль Ideco «Логин» / «Логин и OTP-код»).
- Личный кабинет: вход доменной учётной записью, мастер первичной настройки (только из сетей регистрации, до первой регистрации фактора или по разрешению администратора на 24 ч), вход со вторым фактором (TOTP, push, резервный код), свои факторы, резервные коды, история, сеансы.
- Push-подтверждение с подписью, привязка устройства по одноразовому QR, long-poll, отзыв устройства.
- TOTP (RFC 6238, окно ±1, защита от повтора), выпуск и подтверждение из панели.
- LDAP/AD: LDAPS, несколько контроллеров, вложенные группы, разрешённые группы, пошаговая диагностика.
- Пользователи (локальные и из каталога), группы с сопоставлением группам AD, ресурсы, RADIUS- и API-клиенты (scopes, CIDR, ротация), политики, история входов, «Проблемы входа», аудит с CSV, диагностика.
- Вход администратора: пароль + обязательный TOTP/push, первичная настройка TOTP, CSRF, роли superadmin/admin/operator/auditor, аварийный одноразовый код из консоли.
- REST API v1 с OpenAPI (`/api/docs`), интеграционные запросы доступа.
- mfagatectl: install, admin-init, uninstall, update (копия → образ → миграции → healthy → автооткат), rollback, backup/restore, diagnose, check-update.

## Публикация
- Публичный репозиторий: https://github.com/calleograph/mfa-gate (Apache-2.0, автор Stanislav). Ветка `main` начинается с чистого коммита; полная локальная история — ветка `local-history` (не публикуется).
- Выпуски: v0.2.0, v0.3.0 (текущий). Образ: `ghcr.io/calleograph/mfa-gate:<версия>` публикует `.github/workflows/release.yml` по тегу `vX.Y.Z`; без секретов подписи APK подписываются одноразовым ключом (pre-release).
- Wiki: исходники в `wiki/`, публикация `scripts/publish-wiki.sh` после инициализации Wiki в веб-интерфейсе.

## Известные ограничения
- Не проверено на реальном VPN-шлюзе (Ideco, OpenVPN, NPS) и реальном Android-устройстве; на сервере разработки нет эмулятора/KVM. Приёмка на чистом Ubuntu 26.04 пройдена; обновление 0.2.0 → 0.3.0 и откат на реальной установке — тоже (docs/testing.md).
- Панель по HTTP на всех интерфейсах по умолчанию — требование первичной установки; для недоверенных сетей нужен HTTPS через reverse proxy.
- FCM требует проекта Firebase организации; HMS и UnifiedPush — экспериментально, без проверки на реальных аккаунтах.
- Нет RADIUS Accounting (UDP 1813), режима «пароль+код» в одном поле, атрибутов ответа по группам (Class, Filter-Id), number matching, лимита push на пользователя, RadSec.
- Нет WebAuthn/FIDO2, Kerberos SSO в кабинете, SMTP-приглашений, импорта пользователей группой из каталога.
- Android foss: мгновенные уведомления через постоянное соединение проверены только JVM-тестами и сборкой.

## Проверки
Юнит 60 + сквозные 24 (`server/tests`), LDAP на OpenLDAP 17, Docker-стенд 14/14 этапов, Android 31+31 JVM-тест и сборка APK, панель в браузере на 7 ширинах, нагрузка RADIUS без ошибок, приёмка на чистом Ubuntu Server (установка из GitHub, HTTP, LDAP, RADIUS+push) — подробно в `docs/testing.md`.

## Текущие незавершённые задачи
- Проверка на реальном VPN-шлюзе и Android-устройстве (сценарий — `docs/android.md`).
- Ключ подписи Android и секреты `MFA_GATE_KEYSTORE_*` в репозитории (сейчас тестовые APK).
- Инициализация GitHub Wiki и `scripts/publish-wiki.sh`.
- Следующие этапы видения: RADIUS Accounting и активные VPN-сессии, атрибуты ответа по группам, number matching, WebAuthn, проверка на реальном Ideco с Windows-клиентом.
- Рабочие файлы прежней реализации (Laravel) остаются в корне вне Git: после проверки архива их можно убрать (`_archive/mfa-gate-laravel-2026.06.15.tar.gz`).
