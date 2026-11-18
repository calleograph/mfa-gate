# Участие в разработке

Нужны исправления, проверки на реальных VPN-шлюзах и устройствах, переводы и документация. Перед большой доработкой откройте issue и опишите замысел, чтобы не делать одну работу дважды.

## Структура

| Каталог | Что внутри |
|---|---|
| `server/mfa_gate/` | сервер: `api/` (REST, панель, Device API), `radius/`, `services/` (сценарий входа, политики, LDAP, push), `worker.py` |
| `server/migrations/` | миграции Alembic |
| `server/tests/` | юнит-тесты, LDAP-тесты (`ldap_fixture/`), сквозные тесты (`test_stack.py`) |
| `server/web/` | веб-панель (статические файлы, отдаёт `api`) |
| `android/` | приложение MFA Gate Authenticator ([android/README.md](android/README.md)) |
| `deploy/` | `compose.yml`, `.env.example`, `mfagatectl` |
| `scripts/` | `e2e.sh` (сквозной стенд), `check_private_data.py` (проверка на частные данные) |
| `docs/` | документация; `docs/adr/` — архитектурные решения |

## Окружение сервера

Нужны Python 3.13+ и Docker.

```bash
cd server
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

# PostgreSQL для разработки: только на loopback
docker run -d --name mfa-gate-dev-pg -p 127.0.0.1:55432:5432 \
  -e POSTGRES_USER=mfagate -e POSTGRES_PASSWORD=mfagate -e POSTGRES_DB=mfagate postgres:16-alpine

export MFA_GATE_DATABASE_URL=postgresql+asyncpg://mfa-gate:mfagate@127.0.0.1:55432/mfa-gate
export MFA_GATE_MASTER_KEY="$(.venv/bin/python -m mfa_gate gen-master-key)"   # для разработки; рабочий ключ берётся из файла
export MFA_GATE_PUBLIC_URL=http://127.0.0.1:8080
export MFA_GATE_COOKIE_SECURE=false        # панель по http://, только локально
export MFA_GATE_RADIUS_PORT=18120

.venv/bin/python -m mfa_gate migrate
.venv/bin/python -m mfa_gate create-admin admin   # пароль печатается один раз
.venv/bin/python -m mfa_gate api                  # http://127.0.0.1:8080, OpenAPI: /api/docs
.venv/bin/python -m mfa_gate radius               # в отдельном терминале
.venv/bin/python -m mfa_gate worker               # в отдельном терминале
```

Мастер-ключ, сгенерированный в `export`, живёт только в этой сессии терминала. После перезапуска с другим ключом зашифрованные секреты (TOTP, RADIUS, LDAP) не расшифруются, поэтому сохраните ключ в файл вне репозитория и задавайте `MFA_GATE_MASTER_KEY_FILE`.

Все переменные `MFA_GATE_*` описаны в `server/mfa_gate/config.py`. Команды управления выводит `python -m mfa_gate --help`.

## Тесты

```bash
cd server
.venv/bin/pytest                         # юнит-тесты; LDAP- и сквозные тесты без стенда пропускаются
tests/ldap_fixture/run.sh                # OpenLDAP в Docker (порты только на 127.0.0.1) + pytest -m ldap
cd .. && scripts/e2e.sh                  # изолированный стенд Docker Compose: установка, RADIUS, push, обновление, откат
```

`scripts/e2e.sh` собирает образ, ставит экземпляр со случайными портами на 127.0.0.1 и уникальным `COMPOSE_PROJECT_NAME`, после прогона всё удаляет (`--keep` оставляет стенд). Нужен `server/.venv`.

Android: см. [android/README.md](android/README.md). Коротко: `cd android && ./gradlew :app:testFossDebugUnitTest :app:assembleFossDebug`.

## Стиль

- Python: `server/.venv/bin/ruff check server` (настройки в `server/ruff.toml`). Комментарии и сообщения для администратора на русском.
- Каждое изменение поведения сопровождается тестом. Для RADIUS, push и политик нужен сквозной тест в `tests/test_stack.py`.
- Секреты не пишутся в журналы, в аргументы командной строки и в ответы API, кроме одноразового показа при создании.
- Новая настройка получает безопасное значение по умолчанию. Опасный режим включается явно и журналируется.

## Никаких частных данных

Репозиторий публичный. В коде, тестах, документации и CI допустимы только нейтральные значения:

- домены `example.org`, `example.com`, `example.net`, зона `*.example`;
- адреса `192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24` и частные диапазоны;
- владелец репозитория `calleograph/mfa-gate`;
- никаких названий организаций, реальных логинов, e-mail, путей вида `/home/<имя>`, ключей и токенов.

Перед коммитом запустите:

```bash
python3 scripts/check_private_data.py
```

Если находка — публичный сервис (например, API провайдера push), добавьте домен в `scripts/private-data.allow` и объясните это в описании PR. Если частные данные уже попали в коммит, не исправляйте их новым коммитом поверх: сообщите мейнтейнерам, историю придётся переписать.

## Коммиты и pull request

- Одно логическое изменение на коммит. Сообщение — короткая строка о том, что сделано (`radius: отбрасывать пакет без State при повторе`), при необходимости — абзац о причине.
- PR проходит CI: ruff, pytest, LDAP-тесты, сквозной стенд, сборка Android, проверка на частные данные.
- Изменение поведения или настройки отражается в `CHANGELOG.md` (раздел «Не выпущено») и в документации.
- Отправляя PR, вы соглашаетесь распространять вклад на условиях Apache-2.0.
