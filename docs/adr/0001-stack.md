# 0001. Стек сервера

**Контекст.** Нужен сервер с REST API, RADIUS (UDP, долгие ожидания push), LDAP, криптографией и миграциями. У команды есть проверенные Python-наработки: эталонный проект (FastAPI, SQLAlchemy async, Alembic, ldap3, argon2), донор A (TOTP, SecretBox, LDAP с вложенными группами), донор B (строгий LDAPS, диагностика).

**Решение.** Python 3.13, FastAPI, SQLAlchemy 2 async + asyncpg, Alembic, Pydantic v2, ldap3, cryptography, argon2-cffi, httpx. PostgreSQL 16.

**Альтернативы.** Go (layeh/radius, go-ldap) — быстрее и меньше образ, но параллельная реализация уже отлаженных механизмов и другой стек сопровождения. PHP/Laravel (как MFA-Gate) — неудобен для UDP-сервера с долгими ожиданиями. Нагрузка MFA (десятки–сотни входов в минуту) не требует производительности Go: asyncio держит тысячи ожидающих RADIUS-запросов.

**Последствия.** Перенос кода доноров без переписывания; OpenAPI генерируется FastAPI; один язык для api/radius/worker и CLI.
