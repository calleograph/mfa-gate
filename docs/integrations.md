# Подключение VPN-шлюзов и приложений

Состояние: протокол RADIUS покрыт сквозными тестами на стенде. **Настройки конкретных шлюзов ниже не проверены на реальном оборудовании.** Они собраны по документации производителей и помечены «требует проверки на стенде». Если вы проверили шлюз, пришлите исправления (см. [CONTRIBUTING.md](../CONTRIBUTING.md)).

## Общие правила RADIUS

| Что | Как в MFA Gate |
|---|---|
| Порт | UDP 1812 (`MFA_GATE_RADIUS_PORT` на хосте). Accounting (1813) **не реализован**: выключите его на шлюзе или направьте в другую систему |
| Клиент | определяется по адресу источника пакета. Неизвестный адрес — пакет молча отбрасывается, шлюз видит таймаут |
| Метод пароля | только **PAP**. На CHAP/MS-CHAPv2 MFA Gate отвечает Access-Reject «Поддерживается только PAP». MFA Gate сам проверяет пароль (локально или в LDAP/AD), поэтому ему нужен пароль в открытом виде внутри RADIUS |
| Message-Authenticator | **обязателен** (защита от BlastRADIUS). Пакет без него или с неверным значением отбрасывается, в аудите появляется `radius.bad_authenticator`. Для старого шлюза его можно отключить через `MFA_GATE_RADIUS_REQUIRE_MESSAGE_AUTHENTICATOR=false`, но это ослабляет защиту |
| NAS-Identifier | если задан у клиента в MFA Gate, должен совпадать, иначе пакет отбрасывается |
| Calling-Station-Id | показывается сотруднику в запросе как IP-адрес входа. Настройте шлюз так, чтобы он его передавал |
| Атрибуты ответа | Access-Accept не содержит Class, Filter-Id, VSA. Назначение групп или пулов по ответу RADIUS **не реализовано** |

### Таймаут и повторы

Push держит Access-Request открытым, пока сотрудник не ответит или не истечёт `MFA_GATE_PUSH_TIMEOUT` (по умолчанию 45 с). Поэтому:

- **таймаут RADIUS на шлюзе ≥ `MFA_GATE_PUSH_TIMEOUT` + 15 с**, при значении по умолчанию — 60 с;
- 1–2 повтора. Повтор того же пакета (тот же адрес, Identifier и Request Authenticator) в течение 90 с не создаёт новый вход и не шлёт второй push: MFA Gate ждёт ту же обработку и отвечает тем же. Новый Access-Request того же пользователя к тому же ресурсу с того же адреса, пока push ещё ждёт ответа, присоединяется к этому push;
- если шлюз ограничивает таймаут (например, 10 с), уменьшите `MFA_GATE_PUSH_TIMEOUT` или используйте TOTP через Access-Challenge.

Если таймаут шлюза меньше времени ответа, сотрудник подтверждает вход на телефоне, а VPN-клиент уже показывает ошибку.

### Access-Challenge (код TOTP)

Если политика ресурса допускает TOTP, а push не используется (у пользователя нет телефона или в политике только `totp`), MFA Gate отвечает:

```
Access-Challenge
  Reply-Message  = "Введите одноразовый код"
  State          = <16 байт, идентификатор попытки>
  Session-Timeout = 120        (MFA_GATE_OTP_CHALLENGE_TIMEOUT)
```

Шлюз показывает Reply-Message и отправляет новый Access-Request с тем же `User-Name`, атрибутом `State` и кодом в `User-Password`. Код проверяется один раз, повтор того же кода отклоняется (`invalid_otp`).

Reply-Message передаётся в UTF-8 на русском. Проверьте, что клиент VPN показывает его без искажений.

Режим «пароль и код в одной строке» и режим «только второй фактор» (пароль проверяет шлюз или NPS) по RADIUS **не реализованы**.

### Подготовка в MFA Gate

1. Создайте ресурс, например «VPN офис».
2. Создайте RADIUS-клиента для адреса шлюза (как его видит MFA Gate, с учётом NAT). Скопируйте секрет: он показывается один раз.
3. Откройте UDP 1812 только для адреса шлюза ([install.md](install.md#firewall-для-radius)).
4. Проверьте вход тестовым пользователем и посмотрите историю входов: канал `radius`, ресурс, причина.

Ниже `198.51.100.20` — адрес сервера MFA Gate, `ТЕКСТ_СЕКРЕТА` — секрет из панели.

## OpenVPN (сервер сообщества 2.6)

Требует проверки на стенде.

### Вариант 1: openvpn-plugin-auth-pam + pam_radius

Плагин проверяет пароль в отдельном процессе (отложенная аутентификация), поэтому ожидание push не блокирует остальных клиентов.

```
# server.conf
plugin /usr/lib/openvpn/openvpn-plugin-auth-pam.so openvpn
verify-client-cert require      # или none, если только логин/пароль
auth-gen-token 28800            # иначе повторная аутентификация каждый час (reneg-sec) снова запросит push
hand-window 75                  # время на рукопожатие с аутентификацией, больше MFA_GATE_PUSH_TIMEOUT
```

```
# /etc/pam.d/openvpn
auth    required  pam_radius_auth.so
account required  pam_permit.so
```

```
# /etc/pam_radius_auth.conf: сервер[:порт]  секрет  таймаут
198.51.100.20:1812  ТЕКСТ_СЕКРЕТА  60
```

- pam_radius до версии 3.0 не отправляет Message-Authenticator. С такой версией пакеты будут отброшены. Обновите модуль или (временно) отключите проверку в MFA Gate.
- Коды TOTP: в конфигурации клиента `static-challenge "Код из приложения" 1`. Клиент передаёт пароль и код вместе, плагин отдаёт код модулю PAM, когда pam_radius получит Access-Challenge. Эту цепочку нужно проверить на вашей версии плагина и клиента.

### Вариант 2: radiusplugin

`radiusplugin` (сторонний, давно не обновлялся) умеет accounting и атрибуты, но Access-Challenge не поддерживает. Как он ведёт себя с отложенной проверкой и долгим ожиданием, не проверено.

## Windows Server: NPS / RRAS

Требует проверки на стенде.

Схема: RRAS → NPS → группа удалённых RADIUS-серверов → MFA Gate. MFA Gate должен получить пароль по PAP. Клиенты Windows VPN (SSTP, IKEv2, L2TP) обычно используют MS-CHAPv2 или EAP, а из них пароль не извлечь. **Если NPS проверяет MS-CHAPv2 сам и пересылает только имя, вход работать не будет**: режим «только второй фактор» по RADIUS не реализован.

Настройки NPS:

1. **Remote RADIUS Server Groups** → новая группа → сервер `198.51.100.20`, общий секрет.
2. Вкладка **Load Balancing** сервера: «Number of seconds without response before request is considered dropped» — **60**; «Maximum number of dropped requests before server is identified as unavailable» — не меньше 2.
3. **Connection Request Policies**: пересылать запросы (Forward requests) в эту группу.
4. В RRAS: **Security → Authentication provider: RADIUS → Configure**: таймаут **60** с.
5. Проверьте, что NPS добавляет Message-Authenticator в пересылаемые запросы (в аудите MFA Gate не должно быть `radius.bad_authenticator`).

## Ideco UTM / NGFW

Требует проверки на стенде.

Ideco подключает MFA Gate как внешний каталог RADIUS:

1. **Пользователи → Внешние каталоги → RADIUS**: адрес `198.51.100.20`, порт 1812, секрет.
2. **VPN-подключения → Доступ по VPN**: добавить группу RADIUS и сделать RADIUS каталогом по умолчанию для нужного правила.
3. Встроенную двухфакторную аутентификацию Ideco для этого правила не включайте: второй фактор даёт MFA Gate.

Что проверить на стенде:

- для каких протоколов (IKEv2, L2TP/IPsec, SSTP, Ideco Client) Ideco отправляет пароль по PAP;
- таймаут ожидания ответа RADIUS: в публичной документации его нет. Если он меньше 60 с, уменьшите `MFA_GATE_PUSH_TIMEOUT`;
- отправляет ли Ideco Message-Authenticator;
- показывает ли Ideco Client окно ввода кода по Access-Challenge и в какой кодировке выводит Reply-Message;
- что делает Ideco с пользователем, которого нет в его локальной базе (заводит ли его по ответу RADIUS).

## Другие шлюзы

Ключевые настройки. Всё требует проверки на стенде.

| Шлюз | Где настраивать | Что важно |
|---|---|---|
| Cisco ASA / Secure Client | `aaa-server MFAGATE protocol radius`; `aaa-server MFAGATE (outside) host 198.51.100.20` → `key`, `timeout 60`, `authentication-port 1812` | Access-Challenge показывается клиентом как запрос кода. Accounting в туннельной группе не включайте |
| FortiGate | `config user radius` → `set server`, `set secret`, `set auth-type pap`; `config system global` → `set remoteauthtimeout 60` | По умолчанию `remoteauthtimeout` 5 с, с ним push не успеет. FortiClient показывает запрос кода по challenge |
| MikroTik RouterOS | `/radius add service=ppp address=198.51.100.20 secret=… timeout=…`; в PPP-сервере разрешить PAP | По умолчанию таймаут около 1 с, а верхний предел зависит от версии (сообщают о пределе около 10 с). Access-Challenge RouterOS, по имеющимся сведениям, не поддерживает. Если таймаут не поднимается выше 10 с, push не уложится, а коды TOTP по challenge не сработают. Проверьте на своей версии; для MikroTik пока надёжного сценария нет |
| pfSense | System → User Manager → Authentication Servers → RADIUS: протокол PAP, Authentication Timeout **60** | Access-Challenge в OpenVPN-сервере pfSense не поддерживается, работает только push |

## REST API для своих приложений

Если приложение само принимает логин и пароль, оно может спросить MFA Gate через REST API. Протокол RADIUS для этого не нужен.

1. В панели создайте ключ API с правом `auth:write`. Если нужно, привяжите ключ к ресурсу и ограничьте адресами. Секрет показывается один раз.
2. Сохраните ключ в файле с правами 600. Не передавайте его в аргументах командной строки: аргументы видны другим пользователям в списке процессов.

```bash
# /etc/mfa-gate/api.curl (права 600): параметры curl, ключ в формате key_id:secret
user = "tf_XXXXXXXXXXXX:СЕКРЕТ"
```

Запрос входа:

```bash
curl -fsS -K /etc/mfa-gate/api.curl -H 'Content-Type: application/json' \
  -d @- https://mfa.example.org/api/v1/access-requests <<'EOF'
{"login": "ivanov", "password": "<пароль>", "source_ip": "203.0.113.10", "client_name": "Портал"}
EOF
```

Ответ:

```json
{"id": "6f1c…", "status": "pending", "reason": null, "message": "", "method": "push", "expires_at": "2026-11-18T21:01:00+00:00"}
```

| `status` | Что делать |
|---|---|
| `accepted` | пустить |
| `rejected` | отказать; `reason` — код причины ([admin-guide.md](admin-guide.md#история-входов)) |
| `pending` | ждать решения на телефоне: `GET /api/v1/access-requests/{id}?wait=30` (long-poll до 30 с, повторять до `expires_at`) |
| `challenge` | спросить у пользователя код и отправить `POST /api/v1/access-requests/{id}/otp` с `{"code": "123456"}` |

```bash
curl -fsS -K /etc/mfa-gate/api.curl "https://mfa.example.org/api/v1/access-requests/$ID?wait=30"
```

- Поле `password` не передаётся, если пароль уже проверило само приложение. Тогда MFA Gate проверяет только второй фактор (пользователь должен быть известен MFA Gate).
- Код можно передать сразу в поле `otp` первого запроса.
- `resource_id` в запросе важнее ресурса, привязанного к ключу.
- Если на ожидании `pending` время вышло, повторный `GET` вернёт `rejected` с причиной `push_expired` (после того как фоновый процесс отметит истечение, обычно до 10 с).

Подробности: [api.md](api.md), OpenAPI: `https://mfa.example.org/api/docs`.

## Что проверить на реальном шлюзе

- [ ] Пакеты доходят: в RADIUS-клиенте MFA Gate обновляется «последний запрос», в журнале `radius` нет «неизвестного клиента».
- [ ] Шлюз отправляет Message-Authenticator (нет `radius.bad_authenticator` в аудите).
- [ ] Пароль приходит по PAP (нет ответа «Поддерживается только PAP»).
- [ ] Неверный пароль: отказ приходит сразу, причина `ldap_password` или `local_password`.
- [ ] Push: подтверждение за 30–40 с — VPN подключается. Шлюз не обрывает ожидание раньше.
- [ ] Push: «Отклонить» — отказ приходит сразу, причина `push_denied`.
- [ ] Нет ответа: отказ по истечении `MFA_GATE_PUSH_TIMEOUT`, а не таймаут шлюза.
- [ ] Повторы шлюза не дают второго уведомления на телефон.
- [ ] В запросе на телефоне виден правильный IP клиента (`Calling-Station-Id`).
- [ ] Access-Challenge: клиент показывает запрос кода, кириллица читается, код принимается, повтор кода отклоняется.
- [ ] Переподключение и повторная аутентификация (renegotiation) не запрашивают push слишком часто.
- [ ] Шлюз не требует Accounting для успешного входа.
