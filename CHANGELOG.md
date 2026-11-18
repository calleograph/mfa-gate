# Журнал изменений

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии — [Semantic Versioning](https://semver.org/lang/ru/). Версия продукта — файл `VERSION`; сервер (`server/mfa_gate/config.py`) и Android (`android/VERSION`) выпускаются вместе тегом `vX.Y.Z`.

## [Не выпущено]

## [0.2.0] — 2026-11-19

Объединение с прежним MFA Gate и установка All-in-One.

### Добавлено
- Установка одной командой `sudo ./install.sh` на Ubuntu/Debian: проверка сервера, установка Docker, конфигурация, свободные порты, запуск, первый администратор.
- Первый администратор с временным паролем и обязательной сменой при первом входе; смена собственного пароля в панели.
- Работа по HTTP для первичной настройки (`http://IP:8080`); за HTTPS reverse proxy cookie получают флаг `Secure` автоматически.
- Интеграция OpenVPN без RADIUS: `integrations/openvpn/mfa-gate-openvpn.sh` (отложенная проверка, static-challenge для TOTP).
- `mfagatectl admin-init`, `mfagatectl uninstall`; при недоступном реестре образ собирается из исходников.
- Нагрузочный тест RADIUS `scripts/radius_load.py`.
- Android: приложение MFA Gate Authenticator, тема «Стекло», подключение к HTTP-серверу по явному подтверждению.

### Изменено
- Единое название MFA Gate: пакет `mfa_gate`, переменные `MFA_GATE_*`, утилита `mfagatectl`, образ `ghcr.io/calleograph/mfa-gate`, схема привязки `mfagate://`, строка подписи `mfa-gate-decision-v1`.
- Ограничение частоты входа хранится в PostgreSQL и общее для всех экземпляров api.
- Веб-панель в теме «Стекло».

### Удалено
- Прежняя реализация на Laravel (до 2026.06.15) — заменена новой; данные старых установок не переносятся автоматически.

## [0.1.0] — 2026-11-18

Первый фундамент.

### Добавлено

- RADIUS-сервер: PAP, Access-Challenge для TOTP, обязательный Message-Authenticator, распознавание повторов шлюза, ожидание push в пределах `MFA_GATE_PUSH_TIMEOUT`.
- Сценарий входа: блокировка после ошибок, первый фактор (локально или LDAP/AD), политика, второй фактор. Стабильные коды причин отказа.
- Push-подтверждение с подписью ECDSA P-256 из Android Keystore. Доставка через FCM, HMS и UnifiedPush (HMS и UnifiedPush экспериментальны), long-poll без push.
- TOTP по RFC 6238 с защитой от повторного использования кода.
- LDAP / Active Directory: несколько контроллеров, LDAPS/STARTTLS, вложенные группы, диагностика.
- Политики по ресурсам и группам: `mfa`, `allow`, `deny`, методы, `on_unavailable`.
- REST API v1 (OpenAPI), ключи API со scopes и ограничением адресов, Device API v1.
- Веб-панель: роли `superadmin`, `admin`, `operator`, `auditor`. Второй фактор обязателен для входа.
- Журнал аудита с выгрузкой CSV, история входов, экран проблем входа.
- Docker Compose и `mfagatectl`: установка, обновление с автооткатом, откат, резервные копии, восстановление, диагностика, аварийный код администратора.
- Android-приложение MFA Gate Authenticator: сборки `gms` (FCM) и `foss` (без сервисов Google).
- CI: ruff, pytest, LDAP-тесты, сквозной стенд, сборка Android, проверка на частные данные. Выпуск образа и APK по тегу.

### Не реализовано

- RADIUS Accounting, атрибуты ответа (Class, Filter-Id, VSA).
- Режим «только второй фактор» по RADIUS.

[Не выпущено]: https://github.com/calleograph/mfa-gate/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/calleograph/mfa-gate/releases/tag/v0.1.0
