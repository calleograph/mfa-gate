# Multifactor и VPN-интеграции: исследование для MFA Gate

Дата: 2026-11-18. Статус: исследование, не спецификация.

## 0. Источники и оговорки

- Файлы `rukovodstvo-administratora.pdf`, `rukovodstvo-polzovatelya.pdf`, `formulyar.pdf`,
  «текст инструкции.txt», «как.txt» на сервере **не найдены** (повторный поиск:
  `find / -xdev -iname '*.pdf' | grep -iE 'rukovod|formul|multif|руковод|формуляр'`,
  поиск `*инструкц*` и `как.txt` в `~` и `/srv`). Ссылки на страницы этих PDF
  (админ. стр. 219–220, 291–300, 323–330; формуляр 5–12; польз. 6.1–6.3) **не проверены**.
- Документ собран по публичной документации: multifactor.ru/docs (разделы RADIUS adapter V1/V2,
  LDAP adapter, SSP, HTTP API, способы аутентификации, регистрация, кластер, Ideco),
  README репозиториев MultifactorLab на GitHub, docs.ideco.ru и техническому описанию Ideco VPN 17,
  документации и сообществу Fortinet/MikroTik. Где факт взят из общих знаний о продуктах
  и не подтверждён страницей, он помечен **[проверить]**.
- **Лицензии Multifactor.** Код адаптеров (RADIUS, LDAP, SSP) опубликован на GitHub, но README
  прямо говорит: код дан для ознакомления, лицензия **не разрешает изменять его и создавать
  производные продукты**. MFA Gate не берёт оттуда код, имена классов и структуру файлов
  конфигурации. Берём только функциональную модель (что умеет продукт), её копировать можно.

## 1. Компоненты Multifactor

| Компонент | Роль | Где работает |
|---|---|---|
| Облачный сервер авторизации (`api.multifactor.ru`, облачный RADIUS `radius.multifactor.ru:1812/udp`) | Хранит пользователей, группы, политики, способы подтверждения; создаёт и решает запросы доступа; рассылает push/Telegram/SMS | SaaS (есть и on-premise редакция) |
| RADIUS Adapter (Windows, Linux; V1 и V2) | Локальный RADIUS-сервер: проверяет 1-й фактор в каталоге или NPS, затем 2-й фактор через API (HTTPS 443) | Сеть заказчика |
| LDAP Adapter (Windows, Linux) | LDAP/LDAPS-прокси перед каталогом: перехватывает bind (Simple/NTLM/Digest) и запрашивает 2-й фактор; сервисные учётки (`ldap-service-accounts`) без 2FA | Сеть заказчика |
| Self-Service Portal (SSP) | Пользователь сам регистрирует 2-й фактор, меняет просроченный пароль, управляет ActiveSync-устройствами; SSO по SAML/OIDC с выборочным 2FA по группам AD; CAPTCHA | Сеть заказчика, можно публиковать наружу |
| IIS Adapter | Модуль IIS для OWA / Exchange ActiveSync (в документации только в базе знаний) | Сервер Exchange |
| Windows Logon | Credential provider: 2FA при входе в Windows и RDP | Рабочие станции/серверы |
| DirectorySync | Синхронизация пользователей из группы AD (логин, имя, email, телефон) в облако | Сеть заказчика |
| Мобильное приложение Multifactor | Push «подтвердить/отклонить», встроенный OTP-генератор | iOS/Android |
| Боты Telegram (@MultifactorBot) и eXpress | Push-аналог через сообщение с двумя кнопками | Мессенджеры |

### 1.1 Как RADIUS Adapter проверяет первый фактор

Ключ `first-factor-authentication-source`:

- **ActiveDirectory** (V1) / **Ldap** (V2): адаптер сам делает bind в каталог (AD, Samba, FreeIPA,
  OpenLDAP, ALD Pro, MultiDirectory). Проверяются пароль, блокировка, часы входа, необходимость
  смены пароля; поддерживаются несколько доменов/лесов с доверием. Работает **только с PAP**:
  пароль должен прийти открытым в `User-Password`.
- **Radius**: адаптер проксирует Access-Request во внешний RADIUS (обычно NPS,
  `nps-server-endpoint`, таймаут NPS в V2 по умолчанию 5 с). Так поддерживаются
  MS-CHAPv2/EAP: «при проксировании к NPS протокол может быть любым». Добавляются сетевые политики NPS.
- **None**: пароль не проверяется, запрашивается только 2-й фактор (пароль уже проверило устройство).

В облако уходит только логин, пароль не передаётся. После 1-го фактора адаптер вызывает API
и **держит RADIUS-запрос открытым**, пока пользователь не ответит на push.
Таймаут API `multifactor-api-timeout` по умолчанию **65 с** (меньше задаётся явно, `00:00:45!`).

### 1.2 Ключевые параметры конфигурации (смысл, а не формат)

| Возможность | V1 | V2 |
|---|---|---|
| Доступ только членам группы | `active-directory-group` | `access-groups` (или `deny-groups`) |
| 2FA только для группы | `active-directory-2fa-group` | `second-fa-groups` |
| Группа без 2FA | (bypass-группа) | `second-fa-bypass-groups` |
| Поведение при недоступном API | `bypass-second-factor-when-api-unreachable` (**по умолчанию true = пропускать!**) | то же + `bypass-...-unreachable-groups` |
| Кэш 2-го фактора | `authentication-cache-lifetime` (hh:mm:ss) | то же, ключ кэша = Calling-Station-Id + имя конфигурации клиента + User-Name; `authentication-cache-groups` |
| Приватность | — | `privacy-mode`: `Full` (не передавать ФИО, email, IP) или `Partial:Name,Email,Phone,RemoteHost` |
| 2FA до пароля (защита от перебора и блокировок AD) | `pre-authentication-method` | `none` / `any` / `otp` (пароль+OTP в одном поле), `invalid-credential-delay` 3–6 с |
| Идентификатор для облака | `use-attribute-as-identity` | `identity-attribute` (UPN, mail, sAMAccountName), `requires-upn` |
| Телефон для SMS | `use-active-directory-(mobile-)user-phone` | `phone-attributes` |
| Вложенные группы | `load-active-directory-nested-groups` | `load-nested-groups`, `nested-groups-base-dn` |
| Атрибуты ответа | `<RadiusReply>`: `name`, `value`/`from` (LDAP-атрибут, `memberOf`), условие `when="UserGroup=..."` / `UserName=...` | то же; словарь с VSA вендоров |
| Несколько RADIUS-клиентов | один конфиг | каталог `clients/*.config`, клиент опознаётся по IP / NAS-IP / NAS-Identifier |
| Белый список источников | — | `ip-white-list` по Calling-Station-Id |
| Смена просроченного пароля | `access-challenge-password` через Access-Challenge (нужен LDAPS и право Reset Password) | то же |
| Регистрация новых | `sign-up-groups` (группы для зарегистрировавшихся через адаптер) | то же |
| Журналы | файл, Syslog/SIEM (RFC3164/5424, JSON, ECS) | то же + CorrelationId, env-переопределения `RAD_APPSETTINGS__*` |

**Сопоставление атрибутов** = (а) какой атрибут каталога считается Identity в облаке,
(б) какие RADIUS-атрибуты (Class, Filter-Id, VSA вроде `Fortinet-Group-Name`, `Ideco-*`)
вернуть в Access-Accept по группе. Это даёт VPN-шлюзу роль/пул адресов.

### 1.3 Отказоустойчивость

- Облако: несколько адресов в `multifactor-api-url` через `;`, HTTP-прокси.
- Адаптер: кластер Active/Passive на keepalived (VRRP, плавающий IP, проверка процесса каждые 2 с)
  или балансировщик с привязкой клиента к узлу: повторная передача того же RADIUS-запроса
  должна попасть на тот же узел, иначе будет два push.
- Деградация: bypass при недоступном API (по умолчанию включён). Для MFA Gate это небезопасное
  умолчание, у нас по умолчанию **fail-closed**, а bypass задаётся явно по группам.
- Несколько экземпляров на одном хосте: отдельные службы и порты.

## 2. API Multifactor

- База `https://api.multifactor.ru`, REST/JSON, **HTTP Basic: ApiKey:ApiSecret ресурса**.
  Ответ всегда `{success, message, model}`. `GET /ping` для проверки связи.
- Ключи бывают двух видов: **ключ ресурса** (адаптер, сайт) и **ключ «расширенного API»** проекта
  (управление пользователями, ресурсами, журналом, его нужно включить в настройках).

| Группа | Эндпоинты (по документации) |
|---|---|
| Запрос доступа | `POST /access/requests` (identity, email, phone, language, claims, `callback.action` URL возврата, `callback.target` для iframe, resourceId/resourceName) → `{id, url}` страницы подтверждения; `GET /access/requests` история (фильтры createdAt, identity, resource, offset/limit ≤1000); `GET /v2/access/requests/{id}` |
| Пользователи | `POST/GET /users`, `GET /users/count`, `PUT /users/{id}`, `PUT /v2/users/{id}`, `DELETE`, `POST /v2/users/{id}/lock|unlock`, `POST /users/{id}/enroll` (ссылка настройки с `Ttl`, письмо при наличии email), группы `/v2/users/{id}/groups`, телефоны (≤5), customfields, аутентификаторы `/users/{id}/authenticators[/{type}/{id}]` (регистрация TOTP/HOTP-токена, перенос, копирование) |
| Ресурсы | `GET /resources/types` (26 типов: Cisco, FortiGate, Mikrotik, OpenVPN, RRAS, NPS, CheckPoint, UserGate, NGate, STerra, Linux, SUDO, Winlogon, SAML, OIDC, Wifi…), CRUD `/resources` |
| Журнал, администраторы, SDK | отдельные разделы |

**Статусы.** Запрос: `AwaitingAuthentication` → `Granted` | `Denied` (в журнале есть
authenticator, ip, geo, remoteHost, twoFADeviceIp). Пользователь описан флагами `isEnrolled`,
`isLocked` и полем `lastLogin`.

**Callback.** Это не вебхук, а возврат браузера: после подтверждения пользователь уходит на
`callback.action` с `accessToken` (JWT, `iss=https://access.multifactor.ru/`, `aud=ApiKey`,
`sub=identity`, `jti=id запроса`, exp ≈ iat+300 с, плюс переданные claims). Подпись HS256
(ApiSecret) или **RS256 с JWKS** `/.well-known/jwks.json`, рекомендуется RS256.
Серверных вебхуков о событиях в публичной документации нет, события берутся из журнала/Syslog.

**Модель ресурсов и политик.**
- *Ресурс* = защищаемая система (VPN-шлюз, сайт, SSH…) со своей парой ключей и поведением:
  `registration` (Register|Decline неизвестного пользователя),
  `enrollment` (InlineEnrollment|Deny, если у пользователя нет фактора),
  `authentication` (UseDefaultProvider|NotifyAndLetChoose), язык.
- *Группа* (системная AllUsers + свои) несёт *политику*: разрешённые способы (можно запретить SMS),
  маскирование логина в уведомлениях, доступ к ресурсам. Пользователь в нескольких группах получает
  объединение, строгие флаги срабатывают при включении хотя бы в одной группе.
- Группы каталога (AD) работают локально в адаптере, облачные группы задаются в кабинете.

## 3. Подтверждение, приложение, регистрация

**Способы:** push в приложении Multifactor; WebAuthn/FIDO2/U2F (биометрия, токены);
Telegram-бот; eXpress-бот; аппаратный OTP (TOTP/HOTP, RuToken OTP, JaCarta); программный OTP
(приложение Multifactor, Яндекс Ключ, Google/Microsoft Authenticator); SMS/звонок (помечены как
устаревшие, отключаются политикой). Лимит OTP: 3 попытки на запрос, окно 90 с на пользователя и область.

**Что видит пользователь.** Push с кнопками «Подтвердить / Отклонить». Публичная документация
не перечисляет поля карточки. Журнал хранит ресурс, IP, страну/город, время, поэтому их
показ вероятен **[проверить по руководству пользователя 6.1–6.3]**. Логин в уведомлении
можно маскировать.
Для MFA Gate: показывать ресурс, логин (маскируемый), IP и город, время, а при подозрении
добавлять **number matching** (двузначное число на экране VPN-клиента нельзя показать в RADIUS,
поэтому только в веб-сценариях или в Reply-Message).

**Telegram как фактор.** Пользователь открывает ссылку-приглашение, нажимает Start у бота,
бот привязывает chat_id. Подтверждение: сообщение с inline-кнопками, ответ через callback_query.
Плюсы: не нужно своё приложение и FCM/APNs. Минусы: нужен исходящий доступ сервера к
api.telegram.org (риск блокировок, прокси), фактор привязан к аккаунту Telegram, а не к устройству.
Корпоративная альтернатива: бот eXpress/Mattermost/Rocket.Chat. В MFA Gate Telegram стоит делать
**первым push-каналом MVP**, потому что не нужно публиковать приложение в сторах.

**Регистрация (enrollment):**
1. Ссылка по email или в ответе API (`/users/{id}/enroll`, TTL, в примере 3 ч).
2. Inline enrollment в VPN/VDI-диалоге через Access-Challenge: пользователю без фактора
   предлагают выбрать push, Telegram, OTP или SMS прямо в окне VPN-клиента
   (источники: AD, NPS, локальные).
3. SSP (самостоятельно, только для пользователей AD/LDAP).
4. В веб/SAML-приложениях на странице доступа.
5. Авторегистрация SMS по телефону из AD.

Массово: DirectorySync из группы AD или CSV/PowerShell через API.

## 4. Ideco UTM / NGFW

- **VPN-протоколы Ideco** (тех. описание Ideco VPN 17/18): IKEv2/IPsec, L2TP/IPsec, SSTP, PPTP,
  собственный Ideco Client (Windows; macOS с v17, Linux с v18), для Android strongSwan.
  **OpenVPN и WireGuard в списке client-to-site нет.** Аутентификация: локальная база, AD,
  ALD Pro, Samba DC, внешний каталог RADIUS. 2FA встроены: TOTP, SMS Aero, Мультифактор, RADIUS.
- **Интеграция Multifactor, два механизма** (multifactor.ru/docs):
  1. **Через API (встроенный модуль Ideco).** В Ideco «Двухфакторная аутентификация →
     Интеграция с MULTIFACTOR» вводятся ApiKey/ApiSecret. Ideco сама вызывает облачный API
     и ждёт подтверждения. Протокол закрытый, это интеграция по API самого Multifactor.
  2. **Через RADIUS.** «Пользователи → Внешние каталоги → RADIUS» (адрес, порт, secret,
     резервный сервер), затем в «VPN-подключения → Доступ по VPN» добавляется группа RADIUS,
     каталог по умолчанию RADIUS. RADIUS Adapter стоит в сети и проверяет AD (или `None`).
     Атрибуты ответа: VSA Ideco (Vendor ID 39410, атрибуты 30/31, например
     `Ideco-Administrator-Role`) для групп. Тот же механизм даёт 2FA для входа администраторов.
- В правиле «Доступ по VPN» тип 2FA выбирается на правило. Если тип отключить, Ideco **пропустит
  вход без второго фактора** (предупреждение в интерфейсе). Это стоит учесть в нашей инструкции.
  Ideco Client умеет показывать окно ввода кода по запросу NGFW.
- **API Ideco:** REST API есть (docs.ideco.ru, раздел API, токены доступа в NGFW Novum 22)
  **[проверить, есть ли методы управления VPN-пользователями/2FA]**.
- **Таймауты RADIUS у Ideco** в публичной документации **не указаны** [проверить на стенде].
  Встроенная интеграция Multifactor ждёт push, значит таймаут ожидания у Ideco порядка десятков секунд.
- **Вывод для MFA Gate:** путь без доработок Ideco один, это **RADIUS-сервер MFA Gate как
  «внешний каталог RADIUS»** (PAP). Варианты 1-го фактора: MFA Gate проверяет LDAP/AD сам, или Ideco
  проверяет пароль, а MFA Gate работает в режиме `None`/только 2FA. Нужно: словарь VSA Ideco,
  режим «пароль+OTP» и Access-Challenge для OTP (проверить в Ideco Client и нативных клиентах),
  push с ожиданием ≤ таймаута Ideco. Встроенную API-интеграцию «как у Multifactor» без
  участия Ideco сделать нельзя: её список провайдеров закрытый.

## 5. Другие VPN-шлюзы: RADIUS, challenge, таймауты

Общие правила:
- **PAP обязателен**, если MFA Gate сам проверяет пароль в LDAP. При MS-CHAPv2/EAP пароль не
  восстановить: тогда пароль проверяет NPS (MFA Gate как прокси в NPS) или устройство (MFA Gate в режиме «только 2FA»).
- **Повторы RADIUS опасны для push:** NAS после таймаута шлёт повтор (тот же Identifier и
  Authenticator). Сервер должен узнать дубликат и **привязать его к уже ждущему запросу**,
  а не слать второй push. Это обязательное требование к MFA Gate.
- **Access-Challenge** (State + Reply-Message) нужен для OTP вторым шагом и inline enrollment.

| Шлюз | PAP | Access-Challenge | Таймаут по умолчанию → для push |
|---|---|---|---|
| OpenVPN 2.x | да (plugin/script) | статический (`static-challenge`) и динамический CRV1, зависит от клиента | `hand-window` 60 с (TLS-рукопожатие с аутентификацией) → ≤ 50 с ожидания |
| Windows RRAS + NPS | NPS: PAP только если включён; SSTP/IKEv2 обычно MS-CHAPv2/EAP | нет для VPN-клиента Windows | RRAS → RADIUS: 5 с; NPS remote group: 3 с без ответа, 5 потерь → сервер «недоступен» → 60 с **[проверить]** |
| Cisco ASA / AnyConnect (Secure Client) | да | да (Reply-Message показывается как запрос) | `timeout` сервера 10 с → 60 с |
| FortiGate | да (`auth-type pap`; auto пробует PAP/CHAP/MSCHAP) | да (FortiClient/SSL-VPN спрашивает токен) | `config system global` → `remoteauthtimeout` 5 с (1–300) → 60 с |
| MikroTik RouterOS | да для PPP при разрешённом PAP; L2TP по умолчанию MS-CHAPv2 | нет | `/radius timeout` по умолчанию 1100 мс (раньше 300 мс). Сообщают о верхнем пределе около 10 с, интеграторы советуют 60 с. **[проверить на версии]**; push в RouterOS ненадёжен, лучше «пароль+OTP» |
| pfSense (OpenVPN/IPsec) | да (PAP/CHAP/MSCHAP в настройке сервера) | нет | timeout сервера 5 с → 60 с |
| Check Point (Remote Access/Endpoint) | да | да (challenge в Endpoint Security VPN) | ретраи/таймауты через GuiDBedit (`radius_retrant_*`) **[проверить]** |
| Ideco | да (каталог RADIUS) | Ideco Client показывает окно кода | не документирован **[проверить]** |

**OpenVPN подробнее:**
- `--auth-user-pass-verify script via-file|via-env`. Скрипт **блокирует однопоточный сервер**
  на время ожидания push. В 2.6+ скрипт может вернуть код 2 и позже записать результат в
  `auth_control_file` (deferred), тогда сервер не блокируется. Для MFA Gate это основной путь:
  маленький open-source хелпер, который делает POST в MFA Gate и пишет 1/0 в control-файл.
- `openvpn-plugin-auth-pam` (в составе OpenVPN): отложенная проверка в отдельном процессе,
  через PAM можно подключить `pam_radius` и ходить в RADIUS MFA Gate.
- `radiusplugin` (GPLv2, давно не развивается) поддерживает accounting и атрибуты, но
  синхронность и повторы нужно проверять; OpenVPN-AS имеет свой RADIUS.
- Management interface (`client-auth`/`client-deny`) даёт асинхронность без плагинов.
- Таймаут: укладываться в `hand-window` (60 с) с запасом; `reneg-sec` вызывает повторную
  аутентификацию каждый час. Нужны `auth-gen-token` или кэш MFA Gate, иначе push каждый час.

**Windows RRAS/NPS подробнее:** RRAS → (RADIUS) → NPS → Remote RADIUS Server Group → MFA Gate.
В группе для сервера MFA Gate: «секунд без ответа до признания запроса потерянным» поднять до 60,
порог «недоступен» не меньше 2, в политике запросов на подключение «переслать запросы» (forward).
Режим: NPS проверяет MS-CHAPv2 сам, MFA Gate получает только User-Name и делает 2FA (`None`).
Если нужна пересылка с паролем, требуется PAP. Альтернатива: NPS Extension (DLL-плагин NPS,
так работает Azure MFA), это уже код под Windows, **позже**.

**Рекомендация по таймаутам push в MFA Gate:**
- Ожидание push по умолчанию **45 с**, предел настраивается на клиента (NAS) в диапазоне 10–120 с.
  На NAS выставлять таймаут RADIUS ≥ 60 с и 1–2 повтора.
- Ответ Access-Reject приходит сразу при «Отклонить» и по истечении ожидания с понятным Reply-Message.
- Дедупликация повторов по (NAS, Identifier, Authenticator) и короткий кэш одобрений
  (по умолчанию выключен, включается по группе и ресурсу) для шлюзов, которые делают
  несколько Access-Request подряд (IKEv2, SSTP + переподключения).

## 6. Собственный VPN-клиент: целесообразность

**Android.** `VpnService` даёт TUN без root, одновременно активен один VPN, есть always-on и
per-app. Готовые основы:
- `wireguard-android` (Apache-2.0, с GoBackend на wireguard-go MIT): разрешает форк и
  встраивание; товарный знак «WireGuard» использовать нельзя;
- `ics-openvpn` (GPLv2 с дополнительным требованием сменить название и id): производный клиент
  обязан быть GPLv2 и опубликован целиком;
- strongSwan для Android (GPLv2) нужен для IKEv2-шлюзов (Ideco рекомендует именно его).

**Windows.** `wireguard-windows` и `wireguard-go` (MIT); драйвер **Wintun** поставляется подписанным
DLL под отдельной лицензией (распространять можно только без изменений, исходник GPLv2/MIT).
Свой драйвер требует EV-сертификата и подписи Microsoft, поэтому не рассматривается.
OpenVPN Windows (OpenVPN GUI GPLv2, ovpn-dco, tap-windows6) лицензионно допустим, но любой клиент
нужно подписывать (Authenticode) и обновлять.

**Заключение:**
- Клиент **не нужен для сопоставимости с Multifactor**: Multifactor сам VPN не делает, он
  встраивается в чужие шлюзы через RADIUS/LDAP/API. Ценность MFA Gate в сервере, адаптерах и
  приложении-подтверждателе.
- Свой VPN-клиент означает свой VPN-шлюз (WireGuard-сервер с выдачей ключей после MFA, как в
  Firezone/NetBird/Headscale) или клиент чужого протокола (нативные IKEv2/SSTP не встроить).
  Это отдельный продукт: сборка и подпись под 2 ОС, публикация в RuStore и Google Play,
  обновления, поддержка сетей, MDM, аудит криптографии.
- Риски: вендорские протоколы закрыты (Ideco Client, FortiClient, AnyConnect), сторы требуют
  обоснование VpnService, ответственность за утечки трафика и DNS, юридические требования к
  VPN-сервисам в РФ, объём поддержки.
- **Рекомендация:** не в MVP. Отдельное направление «MFA Gate Connect» после стабилизации
  RADIUS-сервера: WireGuard-шлюз, на котором peer включается только после MFA
  (короткоживущие сессии). Клиенты делаются форком wireguard-android и wireguard-windows/Wintun,
  а не OpenVPN, из-за лицензий. Оценка: прототип Android + Linux-шлюз 2–3 месяца,
  промышленный Android + Windows 6–9 месяцев на 2–3 разработчика.

## 7. Функциональная модель MFA Gate

**Сущности MVP:** `Tenant/Project` (одна организация на установку в MVP), `User` (identity,
имя, email, телефон, статус: active / locked / not enrolled, источник: local / LDAP),
`Group` (локальная или привязанная к группе LDAP), `Policy` (на группу: разрешённые факторы,
требовать 2FA / bypass, кэш, маскирование, поведение при недоступности),
`Resource` (тип: RADIUS-клиент, веб-приложение, LDAP; ключ и секрет; поведение registration,
enrollment, authentication), `RadiusClient` (IP/подсеть или NAS-Identifier, shared secret,
таймаут ожидания, источник 1-го фактора, правила атрибутов ответа), `Directory` (LDAP/AD
подключение, bind, атрибуты identity и телефона), `Authenticator` (TOTP, Telegram chat,
push-устройство, WebAuthn), `AccessRequest` (статус, ресурс, IP, NAS, фактор, время,
причина), `EnrollmentInvite` (токен, TTL), `AuditEvent`, `ApiKey`.

| Функция Multifactor | MFA Gate MVP | Позже |
|---|---|---|
| RADIUS-сервер, PAP, источник 1-го фактора LDAP / None | **MVP** | — |
| 1-й фактор через внешний RADIUS (прокси в NPS) | — | v1.x |
| Группы доступа, группы 2FA и bypass-группы из LDAP | **MVP** | — |
| Несколько RADIUS-клиентов с отдельными настройками | **MVP** | — |
| Ожидание push в RADIUS + дедупликация повторов | **MVP** | — |
| Пароль+OTP в одном поле (`pre-authentication otp`) | **MVP** | — |
| Access-Challenge для OTP вторым шагом | **MVP** | inline enrollment через challenge |
| Кэш 2-го фактора (NAS + user + Calling-Station-Id, по группам) | **MVP** | — |
| Поведение при недоступности: fail-closed по умолчанию, bypass по группам | **MVP** | — |
| Атрибуты ответа по группе (Class, Filter-Id, VSA: Ideco, Fortinet, Cisco) | **MVP** (статические + memberOf) | словари вендоров через UI |
| Privacy mode (что хранить и показывать) | **MVP** (маскирование логина, без хранения ФИО) | гибкий Partial |
| Telegram-бот как push | **MVP** | eXpress, Mattermost |
| TOTP (любое приложение-аутентификатор) | **MVP** | HOTP, аппаратные токены, импорт |
| Своё мобильное приложение с push (FCM/RuStore Push) | — | v2 |
| WebAuthn/FIDO2 | — | v1.x (в веб-сценариях) |
| SMS/звонок | — | через подключаемого провайдера |
| Приглашение по ссылке/email, TTL | **MVP** | — |
| Self-service портал (регистрация фактора, сброс по ссылке) | **MVP** (минимальный) | смена пароля AD, ActiveSync |
| HTTP API: пользователи, группы, ресурсы, запросы доступа, журнал | **MVP** (users, groups, resources, access requests) | SDK, OIDC/SAML IdP |
| Веб-2FA для сайтов: access request → страница → JWT RS256 + JWKS | v1.x | — |
| Серверные вебхуки о событиях (подпись HMAC) | v1.x (у Multifactor их нет, наше отличие) | — |
| Журнал + Syslog/JSON | **MVP** | ECS/CEF, SIEM-коннекторы |
| Синхронизация пользователей из LDAP-группы | **MVP** (по расписанию) | — |
| LDAP-прокси-адаптер | — | v2 |
| Windows Logon / RDP, IIS/OWA | — | v2+ |
| Кластер Active/Passive | документированный keepalived | Active/Active с общей БД |
| Собственный VPN-клиент | — | отдельное направление (раздел 6) |

**Минимальный набор для сопоставимости на VPN-сценариях:** RADIUS (PAP) + LDAP-проверка +
группы и политики + Telegram/TOTP + ожидание push с дедупликацией + кэш + атрибуты ответа +
приглашения + журнал + API пользователей. Этого достаточно для Ideco, FortiGate, Cisco ASA,
Check Point, pfSense и OpenVPN. RRAS работает через NPS в режиме «только 2FA», MikroTik
в режиме «пароль+OTP».
