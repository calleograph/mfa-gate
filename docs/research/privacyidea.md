# Исследование: privacyIDEA и pi-authenticator как источник идей для MFA Gate

Источники (ноябрь 2026, `git clone --depth 1`, код только читался):
`privacyidea/privacyidea` (сервер, AGPL-3.0), `privacyidea/pi-authenticator` (приложение, Apache-2.0),
`privacyidea/FreeRADIUS` (плагин rlm_perl, GPL-2.0). Пути ниже даны относительно корней этих репозиториев.
Код **не копируем и не пересказываем построчно**: берём протоколы, параметры и уроки.

---

## 1. Push Token privacyIDEA

Файлы: `privacyidea/lib/tokens/pushtoken.py` (весь протокол), `privacyidea/api/ttype.py` (диспетчер `/ttype/<type>`),
`privacyidea/lib/smsprovider/FirebaseProvider.py` (отправка в FCM), `privacyidea/lib/crypto.py` (`generate_keypair`),
`privacyidea/models/challenge.py` (challenge и transaction_id). Концепция: wiki `concept:-PushToken`, `concept:-pushtoken-poll`.

### 1.1 Двухэтапный rollout

**Шаг 1 (администратор или пользователь, `POST /token/init type=push genkey=1`).** Токен создаётся в состоянии
`clientwait`; в tokeninfo пишется `enrollment_credential` = 20 случайных байт в hex (одноразовый секрет rollout) и
идентификатор push-шлюза. Ответ содержит QR (`pushurl`):

```
otpauth://pipush/<label>?url=<registration_url>&ttl=<мин>&issuer=<issuer>
    &enrollment_credential=<hex>&v=1&serial=<PIPU...>&sslverify=0|1&poll_only=True|False
    [&pin=True][&app_force_unlock=...][&image=<url>]
```

Схема может быть `pia://` вместо `otpauth://` (политика `use_pia_scheme`, чтобы не перехватывали чужие OTP-приложения).
`url` берётся из политики `push_registration_url` (обычно `https://host/ttype/push`), `ttl` (по умолчанию 10 минут) —
сколько ждать шаг 2. `v=1` оставлен «для будущей смены криптографии». Секретов OTP в QR нет.

**Шаг 2 (телефон, `POST /ttype/push`, без аутентификации):** `enrollment_credential`, `serial`, `fbtoken` (FCM
registration token или `NO_FIREBASE_TOKEN` у сборки без Google), `pubkey` (публичный ключ телефона,
SubjectPublicKeyInfo DER в url-safe base64 без PEM-заголовков), новое поле `capabilities`. Сервер проверяет, что токен
в `clientwait` и credential совпадает, удаляет credential, переводит токен в `enrolled`, сохраняет `pubkey` и
`firebase_token`, **генерирует свою пару RSA-4096 на каждый токен** и возвращает телефону `public_key` сервера.

**Ключи:**

| Где | Ключ | Алгоритм | Хранение |
|---|---|---|---|
| Сервер | private_key_server / public_key_server (на токен) | RSA-4096, e=65537 | tokeninfo, приватный зашифрован security module |
| Телефон | приватный ключ токена | RSA-4096, генерация в Dart (pointycastle) | flutter_secure_storage (не неизвлекаемый Keystore-ключ) |
| Сервер | public_key_smartphone | RSA-4096 | tokeninfo, открыто |

Подпись в обе стороны: **RSA PKCS#1 v1.5 + SHA-256** (в коде прямо: «не все Android поддерживают PSS»), подпись в
**base32**. Новая сущность «контейнер» (`privacyidea/lib/containers/smartphone.py`) уже использует ECDSA
**secp384r1** + SHA-256 и URL `pia://container/<serial>?nonce=&time=&url=&key_algorithm=...` — видно, что проект сам
уходит от RSA к ECC.

### 1.2 Отправка challenge через FCM

`create_challenge`: `nonce` = base32 от случайных байт (`geturandom()`), запись в таблицу challenge с
`transaction_id` (20 случайных цифр) и сроком `PushChallengeValidityTime` / `DefaultChallengeValidityTime`
(по умолчанию **120 с**). Затем строится `smartphone_data`:

`nonce, question, serial, title, sslverify, url, signature` (+ `require_presence`, `version=2` для режима выбора,
+ `capabilities` и отдельная `capabilities_signature` над RFC 8785-канонизированным JSON `{capabilities, nonce}`).

**Сервер подписывает** строку `nonce|url|serial|question|title|sslverify[|require_presence]` своим приватным ключом
токена; телефон проверяет подписью публичным ключом сервера, полученным на шаге 2. FCM HTTP v1 (service account JSON,
`google-auth`): блок `data` = весь `smartphone_data`, плюс видимый блок `notification {title, body=question}`,
`android.priority=HIGH`, `ttl=120s`, для iOS `apns-expiration`=+120 с, `apns-collapse-id`. Следствие: **текст вопроса
и подпись проходят через Google** открытыми для FCM.

Если отправка в FCM упала, challenge всё равно создаётся при разрешённом polling (политика `push_allow_polling`
= allow/deny/token), а пользователю добавляется текст «откройте приложение и обновите».

### 1.3 Ответ телефона

`POST /ttype/push`: `serial`, `nonce`, `signature` [+ `decline=1`, `decline_reason`, `presence_answer`].
Подписываемая строка (клиентом, ключом телефона):

```
{nonce}|{serial}                              # одобрение
{nonce}|{serial}|decline[|{decline_reason}]   # отказ
...|{presence_answer}                         # режим выбора правильной кнопки
```

Сервер перебирает **открытые** challenge токена, пересобирает строку и проверяет подпись; совпавший помечается
`otp_valid` (или `declined/cancelled`). Затем клиент входа (веб/RADIUS/плагин) **опрашивает** `/validate/check`
с `transaction_id` (и пустым паролем) — challenge переходит в успех. После ответа срок продлевается на
`PushChallengeFinalizeGrace` (300 с), чтобы клиент успел забрать результат.

Варианты «присутствия» (защита от push-бомбинга): `push_require_presence` — телефон показывает 2–10 кнопок
(буквы или 00–99), правильную видит пользователь на экране входа; `push_code_to_phone` — после одобрения сервер
выдаёт код, который отображается на телефоне и вводится на экране входа. `push_wait=N` — `/validate/check` держит
HTTP-запрос до N секунд, ожидая ответа (для клиентов без опроса, например RADIUS); несовместим с presence.

### 1.4 Poll-only (pull без FCM)

`GET /ttype/push?serial=..&timestamp=<ISO-8601 UTC>&signature=<sig>`; подпись телефона над `{serial}|{timestamp}`.
Сервер требует |timestamp − now| ≤ **1 минута** (`POLL_TIME_WINDOW`), проверяет подпись и возвращает **список**
открытых challenge в том же формате `smartphone_data`, подписанных сервером. Любая ошибка (нет токена, плохая подпись)
возвращается единым «Could not verify signature!» — без раскрытия существования serial. Режим `poll_only` задаётся
при enrollment (шлюз `poll only`), приложение тогда опрашивает каждые **3 с**, пока открыто, и по жесту «потянуть».
Обновление FCM-токена: `POST` `new_fb_token, serial, timestamp, signature({new_fb_token}|{serial}|{timestamp})`,
окно ±5 минут.

### 1.5 Защита от повтора и время жизни

- Challenge одноразовый: ответ принимается только для `is_open()` (не истёк, не отвечен, не отклонён); повторный
  ответ на тот же nonce ничего не даёт. Отдельная проверка для code_to_phone, чтобы повтор не выдал новый код.
- nonce случайный и уникален, поэтому подпись `nonce|serial` не переносится на другой challenge.
- Poll-запрос **не одноразовый**: в окне ±1 мин его можно повторить (вред — только чтение открытых challenge).
- Телефон держит кольцевой буфер из 100 хэшей nonce (`knownPushRequests`), чтобы push и poll не показали один
  запрос дважды, и считает запрос просроченным через 2 мин локально.
- Срок: 120 с на ответ, +300 с на финализацию, очистка «дворником» (`challenge_janitor`).

**Слабые места**, которые MFA Gate не должен повторять: строка с разделителем `|` без экранирования (в `question`/`title`
может оказаться `|` — неоднозначная канонизация); подпись ответа не включает решение явно для одобрения (только
отсутствие слова `decline`) и не включает аудиторию/хост; RSA-4096 генерируется в Dart на телефоне и не живёт в
Keystore; вопрос и данные идут через FCM открытыми.

## 2. TOTP/HOTP

Файлы: `privacyidea/lib/tokens/hotptoken.py`, `totptoken.py`, `HMAC.py`, `privacyidea/lib/tokenclass.py`.

- **Счётчик в БД.** У каждого токена поле `count`. HOTP проверяет `count .. count+count_window` (по умолчанию 10).
  После успеха `inc_otp_counter` ставит `count = совпавший+1` и сразу коммитит («чтобы исключить повторное
  использование»). Ресинхронизация: `sync_window` (1000) и автоматический autosync по двум подряд введённым кодам.
- **TOTP.** Шаг 30 с (или 60), окно `timeWindow` по умолчанию **180 с** симметрично (то есть ±6 шагов при 30 с —
  широко). Сохраняется номер шага последнего успешного кода; код с номером ≤ сохранённого отклоняется как повтор
  («a previous OTP value was used again»). Это же запрещает использовать более ранний неиспользованный код.
  Сохраняется дрейф часов токена `timeShift`, следующая проверка сдвигает окно.
- **Шифрование секретов** (`privacyidea/lib/security/default.py`): файл `encKey` 96 байт = три AES-256-ключа
  (slot 0 token — OTP-секреты и PIN, 1 config — пароли в конфиге, 2 value). Режим **AES-256-CBC + PKCS7, IV 16 байт,
  без MAC**; формат `hex(iv):hex(cipher)`. `encKey` может быть зашифрован паролем (ключ = SHA-256(пароль) — без KDF),
  тогда сервер ждёт ввода пароля при старте. Есть `aeshsm.py` (PKCS#11 HSM). Для MFA Gate: AES-256-GCM с AAD
  (таблица/столбец/id записи), версия ключа в шифротексте, ключ из файла/секрета, KDF при парольной обёртке.

## 3. RADIUS

privacyIDEA сам RADIUS не обслуживает; FreeRADIUS вызывает Perl-модуль `privacyidea_radius.pm` (rlm_perl,
GPL-2.0), который превращает Access-Request в `POST /validate/check` (`user`, `pass`, `realm`, `client`=NAS-IP,
`state`).

- Ответ с `result.value=true` → Access-Accept (+ сопоставление атрибутов из ответа через `mapResponse`).
- `result.status=true, value=false` и есть `detail.transaction_id` → **Access-Challenge**, `State` = transaction_id
  (20 цифр), `Reply-Message` = `detail.message`. Иначе Access-Reject.
- Следующий Access-Request NAS несёт атрибут `State` обратно; плагин декодирует hex и передаёт как `state`; сервер
  принимает `transaction_id` **или** `state` (`api/validate.py`) и ищет challenge по нему и пользователю.
- Состояние хранится только на сервере privacyIDEA (таблица challenge), RADIUS-слой без состояния.
- Push по RADIUS: либо `push_wait` (запрос висит до N с — надо поднимать таймаут NAS), либо Access-Challenge
  «подтвердите на телефоне и нажмите Enter», затем пустой ответ с State проверяет, одобрен ли challenge.

Для собственного RADIUS MFA Gate: State — непрозрачные ≥16 случайных байт, привязанные к (пользователь, NAS, IP
источника) и сроку; дедупликация повторных пакетов по (src, port, Identifier, Authenticator) (RFC 5080), чтобы
повтор NAS во время ожидания push не создавал второй challenge; обязательный **Message-Authenticator** во всех
запросах и ответах (BlastRADIUS, CVE-2024-3596); `push_wait` ограничивать таймаутом NAS и делать в async.

## 4. Политики и управление токенами

Файлы: `doc/policies/*.rst`, `privacyidea/lib/policy.py`. Политика = имя + **scope** (admin, user, authentication,
authorization, enrollment, webui, register, container, token, hardening, conditional_access) + **action** (строковые
пары `ключ=значение`) + условия: realm, resolver, user, adminrealm/adminuser, client (IP/подсети с исключением `-`),
user agent, время (`Mon-Fri: 8-18`), узел кластера, **priority** (меньше — главнее), «дополнительные условия» по
атрибутам пользователя и HTTP-заголовкам, `check_all_resolvers`. Логика внутри scope **аддитивная**, конфликты
решает priority, при равенстве — ошибка конфликта.

Что брать в простую модель «пользователи/группы/ресурсы/политики»:
- Субъект политики = пользователи и группы (группы из LDAP), а не realm/resolver: realm полезен только для
  многоарендности, MFA Gate может обойтись «источником пользователей».
- Ресурс = RADIUS-клиент или приложение (аналог `client` + user agent, но явной сущностью).
- Действие = **типизированный** набор (разрешённые факторы, push с выбором числа, push_wait, срок challenge,
  лимит неудач, rollout-правила), схема pydantic, а не строковые actions.
- Детерминированное разрешение: priority + «запрет главнее» + явный режим по умолчанию; предпросмотр «какая политика
  сработает для пользователя X на ресурсе Y» (у privacyIDEA есть `/policy/check`).
- Время и IP-подсети — дешёвые и полезные условия; узлы кластера и UA-фильтры — не нужны в первой версии.
- Админские права (scope admin с adminuser/adminrealm) заменить ролями.
- Управление токеном: состояния rollout (`clientwait` → `enrolled`), active/revoked/locked, `maxfail`/failcount с
  автосбросом, лимиты числа токенов на пользователя (`max_token_per_user`, `max_active_token_per_user`).

## 5. LDAP resolver

Файл: `privacyidea/lib/resolvers/LDAPIdResolver.py` (на ldap3).
- **Несколько серверов**: `ldap3.ServerPool` с стратегией ROUND_ROBIN/FIRST/RANDOM, `SERVERPOOL_ROUNDS=2`,
  `SERVERPOOL_SKIP=30` с (сервер помечается недоступным), собственный потокобезопасный пул с блокировкой.
- **Кэш**: процессный словарь по resolver_id и функции (`getUserId`, `getUserInfo`, ...), TTL `CACHE_TIMEOUT`
  (по умолчанию 120 с, 0 — выкл), отдельная таблица кэша пользователей в БД (`UserCache`).
- **Атрибуты**: маппинг JSON `{"username":"sAMAccountName","email":"mail","phone":"telephoneNumber",...}`,
  `UIDTYPE` — неизменяемый id (objectGUID для AD, entryUUID для OpenLDAP, или DN) с аккуратным экранированием
  (RFC 4515), группы через атрибут или рекурсивный поиск, `SIZELIMIT`, `TIMEOUT`, `NOREFERRALS`, StartTLS/LDAPS с CA.
- Аутентификация: service-account bind для поиска, bind от имени пользователя для проверки пароля; SIMPLE, NTLM,
  Kerberos.
- Для MFA Gate: хранить объект пользователя по objectGUID/entryUUID (переименования не ломают привязки токенов),
  2–3 сервера с failover, кэш 60–300 с, сопоставление атрибутов в конфиге, группы — для политик.

## 6. pi-authenticator

- **Стек**: Flutter/Dart, Riverpod, `firebase_messaging` + `flutter_local_notifications`, `flutter_zxing`/`zxing2`
  (QR), `pointycastle` (RSA), `flutter_secure_storage`, `local_auth`, `screen_security`, `home_widget`.
- **Ключи**: пара RSA-4096 генерируется в отдельном isolate на pointycastle (`lib/utils/rsa_utils.dart`), PEM
  приватного ключа кладётся в `flutter_secure_storage` (на Android — шифрование ключом из Keystore, но сам ключ
  токена извлекаем и живёт в памяти процесса при подписи). Старый формат хранился в плагине `local_plugins/
  pi-authenticator-legacy` (`SecretKeyWrapper.java`) — миграция тянется до сих пор (зафиксирован `flutter_secure_storage`
  v10 ради миграции).
- **Биометрия**: `local_auth` (на Android — BiometricPrompt), `lib/utils/lock_auth.dart`: блокировка токена/приложения,
  `biometricOnly` по настройке; это гейт UI, **не криптографическая привязка** ключа к биометрии.
- **Уведомления**: foreground `onMessage` и background `onBackgroundMessage` (отдельный isolate) в
  `lib/utils/push_provider.dart`; проверяется подпись сервера (`PushRequest.verifySignature`, `signedData`
  `nonce|url|serial|question|title|sslverify[|...]`), запрос кладётся в защищённое хранилище, показывается локальное
  уведомление. Битые данные → немедленный poll. Дубликаты отсекаются буфером из 100 хэшей nonce.
- **Pull**: таймер 3 с при включённом polling, подпись `serial|timestamp(UTC ISO)` (`pollForChallenge`), и
  ручное обновление. Без Google Play приложение работает в режиме poll-only.

## 7. Лицензии

| Компонент | Лицензия | Совместимость с Apache-2.0 |
|---|---|---|
| privacyIDEA (сервер) | AGPL-3.0 | Нельзя копировать/переводить код. Протокол и идеи — можно (переписать с нуля) |
| privacyidea/FreeRADIUS (rlm_perl) | GPL-2.0 | Нельзя заимствовать код |
| pi-authenticator | Apache-2.0 (LICENSE.txt: «licensed under APL 2.0») | Можно, с сохранением уведомлений; но он на Dart — практически только идеи |
| ...его зависимости: flutter_secure_storage, firebase (FlutterFire), local_auth | BSD-3-Clause | Да |
| ...pointycastle (pc-dart), flutter_zxing | MIT | Да |
| FastAPI, SQLAlchemy, alembic, pydantic, argon2-cffi, pyotp | MIT | Да |
| asyncpg | Apache-2.0 | Да |
| cryptography | Apache-2.0 OR BSD-3-Clause | Да |
| uvicorn, httpx, pyrad (если понадобится RADIUS-кодек) | BSD-3-Clause | Да |
| ldap3 2.9.1 | LGPL-3.0 | Да как библиотека: импорт без изменений; в образе Docker приложить текст LGPL и не мешать замене пакета; свои правки ldap3 — только под LGPL |
| AndroidX Biometric, CameraX | Apache-2.0 | Да |
| ZXing core, zxing-android-embedded (journeyapps) | Apache-2.0 | Да; embedded в режиме сопровождения (последний push 2024) — лучше ZXing core + CameraX ImageAnalysis |
| OkHttp, kotlinx-serialization | Apache-2.0 | Да |
| Firebase Messaging SDK | Apache-2.0 (код SDK) | Лицензионно да, но тянет проприетарные Google Play services и сервис FCM: держать в отдельном build flavor, основная сборка (F-Droid) — poll-only |

Вывод: из privacyIDEA и rlm_perl берём только описание протокола и параметры, пишем с нуля, не открывая их код при
реализации («чистая комната» по этому документу). Имена «privacyIDEA»/«pi» и схему `pia://` не используем (товарные
знаки NetKnights, и чтобы не конфликтовать с их приложением). Совместимость с pi-authenticator по проводу не цель.

## 8. Решения для MFA Gate

1. **QR/URI свой, не `otpauth://`**: `mfagate://enroll?v=1&u=<https base URL>&t=<enroll token>&s=<token id>&kid=<id
   ключа сервера>&fp=<SHA-256 отпечаток ключа сервера, base64url>&exp=<unix>`. `otpauth://` оставить только для
   обычного TOTP (совместимость с любыми приложениями). Своя схема не перехватывается чужими OTP-приложениями;
   отпечаток ключа в QR закрепляет ключ сервера ещё до сетевого обмена (у privacyIDEA ключ сервера приходит по сети
   на шаге 2 и защищён лишь TLS). Enroll token — 32 случайных байта, одноразовый, срок 10 мин, в БД хранится хэш.
2. **Ключи**: телефон — ECDSA P-256 в Android Keystore (StrongBox при наличии), неизвлекаемый; опционально
   `setUserAuthenticationRequired` + BiometricPrompt с CryptoObject — биометрия криптографически обязательна для
   подписи. Сервер — один ключ подписи на инсталляцию (P-256 или Ed25519) с `kid` и ротацией, а не RSA-4096 на токен.
   P-256 ECDSA поддерживается Keystore на всех целевых API и даёт короткие подписи.
3. **Подписываемые данные — канонический JSON (RFC 8785/JCS) или CBOR, а не строка с `|`**, с доменным разделителем
   и всеми полями решения:
   сервер → телефон `{"typ":"mfa_gate.push.challenge.v1","aud":<base URL>,"tid":<token id>,"cid":<challenge id>,
   "nonce":<32 байта b64url>,"iat","exp","ctx":{app, ip, город?, время},"num":<опц. число для сверки>}`;
   телефон → сервер `{"typ":"mfa_gate.push.response.v1","aud","tid","cid","nonce","decision":"approve|deny",
   "reason"?,"num"?,"iat"}`. Решение и аудитория явно в подписи — нет даунгрейда «approve/decline» и переноса на
   другой сервер.
4. **FCM только как «будильник»**: data-only сообщение `{tid, cid}` без вопроса и подписи; телефон забирает
   challenge по TLS. Тогда push и poll — один путь, Google не видит контекст входа, а сборка без Google просто
   опрашивает.
5. **Poll-эндпойнт**: `POST /api/v1/device/challenges` с телом `{"typ":"mfa_gate.poll.v1","tid","iat","nonce"}` и
   подписью; окно ±60 с, nonce запоминается на окно (Redis/таблица с TTL) — запрос одноразовый, в отличие от
   privacyIDEA. Единый ответ ошибки, rate limit на tid и IP. Опрос: 3–5 с только при открытом приложении/экране
   ожидания; в фоне — WorkManager не чаще раза в 15 мин или FCM-будильник.
6. **Replay**: challenge одноразовый (атомарный `UPDATE ... WHERE state='pending' AND exp>now()`), срок ответа
   60–120 с, окно финализации для клиента ≤ 60 с, ответ привязан к cid+nonce. Защита от push-бомбинга по умолчанию:
   выбор/ввод числа (как `require_presence`), лимит активных push на пользователя (1) и частоты.
7. **TOTP**: шаг 30 с, окно ±1 шаг (не 180 с), запоминать последний принятый шаг, разрешать резинхронизацию только
   админу. Секреты — AES-256-GCM с AAD и версией ключа.
8. **RADIUS**: State = случайный opaque id challenge; PAP + Access-Challenge с текстом «подтвердите на телефоне и
   нажмите Enter» или ввод TOTP; `push_wait` в async с лимитом ниже таймаута NAS и дедупликацией повторов;
   обязательный Message-Authenticator.
