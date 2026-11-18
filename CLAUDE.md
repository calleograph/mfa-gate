# MFA Gate — правила работы с проектом

## Перед работой
- Прочитать `PROJECT.md` (текущее состояние) и `HISTORY.md` (журнал), для архитектурных вопросов — `docs/adr/` и `docs/threat-model.md`.
- Протокол телефона — `docs/protocol/device-api.md`: сервер и Android меняются только вместе.

## Навыки (выбирать автоматически, не дожидаясь указаний)
- Любая работа с кодом: `ponytail:ponytail` — сначала причина и существующий код, затем самое простое надёжное решение.
- Проверка изменений: `ponytail:ponytail-review`; ревью всего проекта — `ponytail:ponytail-audit`; безопасность — `security-review` / `web-api-security-audit`.
- Интерфейс панели (`server/web/`): `ui-ux-pro-max` для системы, `impeccable` для критики и полировки; тема — «Стекло» (токены в `server/web/assets/css`), стандарт компоновки: 12-колоночная сетка, отступы 4·8·12·16·24·32·48·64, тап-цели ≥ 44 px. Проверка — `agent-browser` на ширинах 320–1920.
- Docker, установка, выпуск: `docker-shared-host-deploy`, `release-ci-discipline`, `resource-guard` перед тяжёлыми сборками.
- LDAP/AD: `directory-profile-sources`; публичный репозиторий: `public-repo-private-config-hygiene`.

## Обязательно после каждого законченного изменения
- `PROJECT.md` — привести к текущему состоянию (устаревшее заменить).
- `HISTORY.md` — добавить запись в конец: дата, что сделано, основные файлы, результат, известные проблемы. Старые записи не менять.
- `CHANGELOG.md` — пользовательское описание значимых изменений в раздел `[Не выпущено]`.
- Только анализ без изменений файлов — журнал не обновляется.

## Публичный репозиторий
- Никаких реальных доменов, IP, имён серверов, учётных записей, ключей, путей и названий внутренних проектов: только `example.org`, `192.0.2.x`, `198.51.100.x`, `203.0.113.x`.
- Перед коммитом: `python3 scripts/check_private_data.py`.
- `_archive/` (прежняя реализация на Laravel) — локальный архив, в Git не входит.

## Проверки
```bash
cd server && .venv/bin/ruff check mfa_gate tests && .venv/bin/pytest          # юнит
server/tests/ldap_fixture/run.sh                                              # LDAP на настоящем OpenLDAP
scripts/e2e.sh                                                                # Docker-стенд: установка, сквозные, обновление, откат, копии
cd android && ./gradlew :app:testFossDebugUnitTest :app:assembleFossDebug :app:assembleGmsDebug
```
Не править `deploy/` и `scripts/` во время работающего `scripts/e2e.sh`: bash читает скрипты по ходу выполнения.

## Выпуск
Версия — файл `VERSION`, она же в `server/mfa_gate/config.py` и `android/VERSION`; тег `vX.Y.Z` запускает `.github/workflows/release.yml` (образ `ghcr.io/calleograph/mfa-gate:X.Y.Z`, APK, GitHub Release). Без секретов подписи APK подписываются одноразовым ключом, выпуск помечается как предварительный.
