# Подключение VPN

Перед настройкой шлюза выполните [Настройка RADIUS](Настройка-RADIUS.md): ресурс, RADIUS-клиент с адресом шлюза и сохранённый общий секрет.

> **Статус.** Протокол RADIUS MFA Gate покрыт сквозными тестами на стенде. Настройки конкретных шлюзов ниже собраны по документации производителей и **на реальном оборудовании не проверены** — у каждого раздела стоит пометка. Если вы проверили шлюз, пришлите исправления через Issues или Pull Request.

Обозначения: `192.0.2.10` — сервер MFA Gate, `СЕКРЕТ` — общий секрет RADIUS-клиента, `ivanov` — тестовый сотрудник с привязанным телефоном.

Общие требования к любому шлюзу (подробно — [Настройка RADIUS](Настройка-RADIUS.md), шаг 3): PAP, Message-Authenticator, таймаут ≥ 60 с, 1–2 повтора, Accounting выключен.

## Проверка после настройки любого шлюза

1. Вход с верным паролем → телефон получает запрос → «Подтвердить» → VPN подключается.
2. «Отклонить» на телефоне → отказ приходит сразу.
3. Неверный пароль → отказ сразу, push не приходит.
4. Нет ответа на телефоне → отказ через 45 секунд (а не таймаут шлюза раньше).
5. В «Истории входов» — канал `radius`, правильный ресурс, IP сотрудника.

Полный проверочный список: [docs/integrations.md](https://github.com/calleograph/mfa-gate/blob/main/docs/integrations.md#что-проверить-на-реальном-шлюзе).

## OpenVPN: через REST API (скрипт из репозитория)

Самый простой вариант для OpenVPN Community 2.5+: без RADIUS и без модулей PAM. Скрипт `integrations/openvpn/mfa-gate-openvpn.sh` проверяет вход через REST API с отложенной проверкой: ожидание push не блокирует других клиентов. Поддерживает ввод кода TOTP через `static-challenge`.

> Требует проверки на стенде.

### Шаг 1. Ключ API

В панели: «API-клиенты» → «Создать ключ»: «Название» `OpenVPN офис`, «Права ключа» — только `auth:write`, «Ресурс по умолчанию» — ресурс вашего VPN (создайте ресурс типа VPN, если его нет), «Разрешённые адреса» — адрес сервера OpenVPN. Окно после создания покажет «Идентификатор ключа (key_id)» вида `tf_…` и «Секрет» — один раз. В настройках ниже они записываются через двоеточие: `key_id:секрет`.

### Шаг 2. Скрипт и настройки на сервере OpenVPN

```bash
sudo apt-get install -y curl
sudo install -m 755 mfa-gate-openvpn.sh /etc/openvpn/mfa-gate-openvpn.sh
sudo tee /etc/openvpn/mfa-gate.conf >/dev/null <<'EOF'
MFA_GATE_URL=http://192.0.2.10:8080
MFA_GATE_KEY=tf_xxxxxxxxxxxx:СЕКРЕТ_КЛЮЧА
MFA_GATE_WAIT=50
EOF
sudo chown root:nogroup /etc/openvpn/mfa-gate.conf && sudo chmod 640 /etc/openvpn/mfa-gate.conf
```

Файл `mfa-gate-openvpn.sh` возьмите из репозитория: `integrations/openvpn/mfa-gate-openvpn.sh`. После перехода на HTTPS замените `MFA_GATE_URL` на `https://mfa.example.org`.

Скрипт выполняется от имени пользователя OpenVPN после понижения прав, поэтому файл читаем для группы. Укажите вместо `nogroup` группу из строки `group` вашего `server.conf` (часто `nogroup` или `openvpn`). **Как проверить:** `sudo -u nobody cat /etc/openvpn/mfa-gate.conf` выводит содержимое (подставьте пользователя из строки `user`).

### Шаг 3. `server.conf`

```text
script-security 2
auth-user-pass-verify /etc/openvpn/mfa-gate-openvpn.sh via-file
auth-gen-token 0 external-auth
reneg-sec 0
```

`auth-gen-token` нужен, чтобы при смене ключей сессии OpenVPN не спрашивал push заново. Для ввода кода TOTP добавьте в **конфигурацию клиента** (`.ovpn`):

```text
static-challenge "Код MFA (пусто — подтверждение на телефоне)" 1
```

```bash
sudo systemctl restart openvpn-server@server
```

**Ожидаемый результат:** вход клиента ждёт подтверждения на телефоне и подключается после «Подтвердить».

**Как проверить:** `journalctl -t mfa-gate-openvpn` — строки `user=ivanov result=accepted`; в «Истории входов» канал `api`, клиент `OpenVPN …`. Любая ошибка (сеть, ответ сервера, таймаут) приводит к отказу во входе.

## OpenVPN: через RADIUS (PAM)

> Требует проверки на стенде.

```bash
sudo apt-get install -y libpam-radius-auth
```

`/etc/pam_radius_auth.conf` (сервер, секрет, таймаут):

```text
192.0.2.10:1812  СЕКРЕТ  60
```

`/etc/pam.d/openvpn`:

```text
auth    required  pam_radius_auth.so
account required  pam_permit.so
```

`server.conf`:

```text
plugin /usr/lib/x86_64-linux-gnu/openvpn/plugins/openvpn-plugin-auth-pam.so openvpn
verify-client-cert require
auth-gen-token 28800
hand-window 75
```

Путь к плагину зависит от дистрибутива: `dpkg -L openvpn | grep auth-pam`.

- `pam_radius` до версии 3.0 не отправляет Message-Authenticator — пакеты будут отброшены (в журнале аудита `radius.bad_authenticator`). Обновите модуль.
- Коды TOTP через Access-Challenge в этой цепочке (`static-challenge` + плагин PAM) нужно проверять на вашей версии; надёжнее вариант через REST API выше.

## Windows Server: NPS / RRAS

> Требует проверки на стенде.

Схема: RRAS → NPS → группа удалённых RADIUS-серверов → MFA Gate. MFA Gate должен получить пароль по PAP. Клиенты Windows VPN (SSTP, IKEv2, L2TP) обычно используют MS-CHAPv2 или EAP, из которых пароль не извлечь: **если NPS сам проверяет MS-CHAPv2 и пересылает только имя, вход работать не будет** (режим «только второй фактор» по RADIUS не реализован). Подходит для клиентов, которые отправляют пароль по PAP.

1. NPS → RADIUS Clients and Servers → **Remote RADIUS Server Groups** → New → сервер `192.0.2.10`, общий секрет `СЕКРЕТ`.
2. В свойствах сервера, вкладка **Load Balancing**: «Number of seconds without response before request is considered dropped» — **60**; «Maximum number of dropped requests…» — не меньше 2.
3. Policies → **Connection Request Policies** → New: «Forward requests to the following remote RADIUS server group» — группа из шага 1.
4. RRAS → свойства сервера → Security → Authentication provider: **RADIUS** → Configure → сервер NPS, таймаут **60** с.
5. В методах аутентификации RRAS разрешите PAP только для протоколов, где это допустимо.

**Как проверить:** в журнале аудита MFA Gate нет `radius.bad_authenticator` (NPS добавляет Message-Authenticator); в «Истории входов» запрос с ресурсом этого шлюза. RADIUS-клиентом в MFA Gate заводится адрес **сервера NPS**, а не RRAS.

## Ideco UTM / NGFW

> Требует проверки на стенде.

1. **Пользователи → Внешние каталоги → RADIUS**: адрес `192.0.2.10`, порт `1812`, секрет `СЕКРЕТ`.
2. **VPN-подключения → Доступ по VPN**: добавьте группу RADIUS и сделайте RADIUS каталогом для нужного правила.
3. Встроенную двухфакторную аутентификацию Ideco для этого правила не включайте: второй фактор даёт MFA Gate.

Проверьте на стенде:

- для каких протоколов (IKEv2, L2TP/IPsec, SSTP, Ideco Client) пароль уходит по PAP;
- таймаут ответа RADIUS: в публичной документации его нет. Если он меньше 60 с, уменьшите `MFA_GATE_PUSH_TIMEOUT` ([Настройка RADIUS](Настройка-RADIUS.md), шаг 3);
- отправляется ли Message-Authenticator;
- показывает ли Ideco Client окно ввода кода по Access-Challenge и читается ли кириллица.

## Cisco ASA / Secure Client

> Требует проверки на стенде.

```text
aaa-server MFAGATE protocol radius
aaa-server MFAGATE (inside) host 192.0.2.10
 key СЕКРЕТ
 authentication-port 1812
 timeout 60
tunnel-group VPN-USERS general-attributes
 authentication-server-group MFAGATE
```

Accounting в туннельной группе не включайте. Access-Challenge Secure Client показывает как запрос кода.

## FortiGate

> Требует проверки на стенде.

```text
config user radius
    edit "MFA-Gate"
        set server "192.0.2.10"
        set secret СЕКРЕТ
        set auth-type pap
    next
end
config system global
    set remoteauthtimeout 60
end
```

По умолчанию `remoteauthtimeout` — 5 секунд, с ним push не успевает. Добавьте сервер `MFA-Gate` в группу пользователей SSL VPN / IPsec. FortiClient показывает запрос кода по Access-Challenge.

## MikroTik RouterOS

> Требует проверки на стенде. Надёжного сценария пока нет.

```text
/radius add service=ppp address=192.0.2.10 secret=СЕКРЕТ timeout=10s
/ppp aaa set use-radius=yes
/interface l2tp-server server set authentication=pap
```

Таймаут RADIUS в RouterOS по умолчанию около 1 с, а верхний предел, по имеющимся сведениям, около 10 с. Access-Challenge RouterOS, по имеющимся сведениям, не поддерживает. Если таймаут нельзя поднять выше 10 с, push не уложится, а коды TOTP по challenge не сработают. Вариант: уменьшить `MFA_GATE_PUSH_TIMEOUT` до 8 с и просить сотрудников держать приложение открытым — проверьте, подходит ли это вам.

## pfSense

> Требует проверки на стенде.

System → User Manager → Authentication Servers → Add: Type RADIUS, протокол **PAP**, порт 1812, секрет, Authentication Timeout **60**. Выберите этот сервер в настройках OpenVPN-сервера pfSense. Access-Challenge OpenVPN-сервер pfSense не поддерживает: работает только push.

## Свои приложения

Если приложение само принимает логин и пароль, ему не нужен RADIUS: [Интеграция по REST API](Интеграция-по-REST-API.md).
