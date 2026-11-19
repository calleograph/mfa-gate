# HTTPS через reverse proxy

*Для кого: системный администратор, который переводит MFA Gate с HTTP на HTTPS.*

MFA Gate не завершает TLS сам. HTTPS даёт reverse proxy — nginx или Caddy на том же сервере: он принимает соединения на порту 443 и передаёт их в MFA Gate на `127.0.0.1:8080`. В результате кабинет и панель открываются по `https://mfa.example.org/`, а телефоны, если нужно, подтверждают входы через внешний адрес `https://auth.example.org`.

## Зачем это нужно

По HTTP пароли администраторов и доменные пароли сотрудников идут по сети открытым текстом, а сессию панели можно перехватить. Кроме того, адрес сервера попадает в QR-код привязки телефона: телефоны, привязанные по HTTP, после перехода придётся привязывать заново. Поэтому HTTPS настраивают до приглашения сотрудников.

## Когда пригодится

- После первичной настройки, до пилота.
- Сотрудники будут входить в VPN из дома, и телефонам нужен внешний адрес.
- Меняется имя сервера или сертификат.

Весь путь вместе с DNS, периметром и перепривязкой телефонов — [Сценарий: переход на HTTPS и внешний адрес](Сценарий-Переход-на-HTTPS-и-внешний-адрес.md). Эта страница — о настройке прокси и самого MFA Gate.

## Что потребуется

- DNS-имя для кабинета и панели, например `mfa.example.org`, и, для работы из дома, внешнее имя `auth.example.org`.
- Сертификаты. Для адреса, который попадает в QR-код телефона, — только от публичного центра сертификации, например Let's Encrypt: приложение MFA Gate доверяет лишь системным центрам сертификации Android, корпоративный УЦ оно не примет. Для внутреннего имени при двух адресах подойдёт и корпоративный УЦ.
- Свободные порты 80 и 443 на сервере.
- Права `sudo`.

## Один адрес или два

| Схема | Когда | Настройки MFA Gate |
|---|---|---|
| **Один адрес** `mfa.example.org` | телефоны подключаются к офисной сети, пилот, всё внутри | `MFA_GATE_PUBLIC_URL=https://mfa.example.org`, `MFA_GATE_DEVICE_URL` пустой |
| **Два адреса**: `mfa.example.org` и `auth.example.org` | сотрудники входят в VPN из дома: телефон подтверждает вход до подключения VPN и должен достучаться до сервера из Интернета | `MFA_GATE_PUBLIC_URL=https://mfa.example.org`, `MFA_GATE_DEVICE_URL=https://auth.example.org` |

Во второй схеме внешний адрес публикует **только** `/api/device/`, остальное — ответ 404. Кабинет, панель и REST API остаются во внутренней сети. Если имена в двух адресах различаются, MFA Gate и сам отвечает 404 на любой запрос к внешнему имени вне `/api/device/` и `/health/` — на случай ошибки в прокси. Подробно: [Сетевая схема и публикация](Сетевая-схема-и-публикация.md).

## Что важно для MFA Gate

| Требование | Почему |
|---|---|
| Прокси **перезаписывает** `X-Forwarded-For` адресом клиента | MFA Gate берёт последнее значение; адрес нужен для журнала, лимитов входа и ограничений ключей API |
| Прокси передаёт `X-Forwarded-Proto: https` | по нему cookie панели получает флаг `Secure` |
| Прокси передаёт исходный `Host` | по нему MFA Gate отличает внешнее имя |
| Таймаут чтения не меньше 40 с, буферизация выключена | телефоны держат запрос открытым до 30 с; в примерах — 75 с |
| Адрес прокси входит в `MFA_GATE_TRUSTED_PROXIES` | заголовки принимаются только от доверенных адресов. Значение `auto` — loopback и шлюз сети контейнера, то есть прокси на этом же сервере |
| `MFA_GATE_HTTP_BIND=127.0.0.1` | порт 8080 не должен быть доступен снаружи в обход TLS |

## Где найти

- Готовые конфигурации на два адреса: `/opt/mfa-gate/deploy/proxy/nginx.conf` и `/opt/mfa-gate/deploy/proxy/Caddyfile`.
- Фрагменты под вашу установку: **Панель → Адреса и публикация**, блоки **nginx** и **Caddy** с кнопкой **Скопировать**.
- Настройки MFA Gate: `/opt/mfa-gate/deploy/.env`.

## Как это сделать

### Шаг 1. Проверьте имена и порты

```bash
getent hosts mfa.example.org
sudo ss -ltnp '( sport = :80 or sport = :443 )'
```

**Что увидите:** адрес сервера, например `192.0.2.10`; вторая команда печатает только заголовок. Если на сервере уже работает nginx, добавьте к нему только блоки `server` для MFA Gate.

### Шаг 2а. nginx

```bash
sudo apt-get install -y nginx certbot python3-certbot-nginx
nginx -v
```

Получите сертификат внешнего имени, пока сайт MFA Gate не включён:

```bash
sudo certbot certonly --nginx -d auth.example.org
```

**Что увидите:** `Successfully received certificate`. Для одного адреса получите так же сертификат `mfa.example.org`, если имя видно из Интернета, или по проверке DNS-01. Внутреннему имени при двух адресах подойдёт сертификат корпоративного УЦ.

Скопируйте пример и отредактируйте:

```bash
sudo cp /opt/mfa-gate/deploy/proxy/nginx.conf /etc/nginx/sites-available/mfa-gate
sudo editor /etc/nginx/sites-available/mfa-gate
```

В файле замените:

1. имена `mfa.example.org` и `auth.example.org` своими;
2. сети в `allow` — на внутренние сети и пул адресов VPN. Это главная защита внутреннего имени;
3. пути к сертификатам: для внешнего имени — `/etc/letsencrypt/live/auth.example.org/fullchain.pem` и `privkey.pem`, для внутреннего — свои;
4. адрес `127.0.0.1:8080`, если установщик выбрал другой порт.

Для **одного адреса** удалите блок `server` с `server_name auth.example.org` и уберите это имя из блока на порту 80.

Учтите версию nginx из `nginx -v`:

- ниже 1.25.1 — удалите строки `http2 on;` и пишите `listen 443 ssl http2;`. Это касается nginx из пакетов Ubuntu 22.04 и 24.04 и Debian 12;
- ниже 1.19.4 (Ubuntu 22.04) — удалите блок с `ssl_reject_handshake on;`. Внутреннее имя по-прежнему закрыто списком `allow`;
- если на сервере уже есть сайт с `default_server` на 80 или 443, удалите соответствующий блок `default_server` из файла MFA Gate.

Включите сайт:

```bash
sudo ln -sf /etc/nginx/sites-available/mfa-gate /etc/nginx/sites-enabled/mfa-gate
sudo nginx -t && sudo systemctl reload nginx
```

**Что увидите:** `syntax is ok` и `test is successful`. Продление сертификата certbot настраивает сам; проверка: `sudo certbot renew --dry-run`.

### Шаг 2б. Caddy

Caddy сам получает сертификат Let's Encrypt для внешнего имени, сам ставит `X-Forwarded-For` и `X-Forwarded-Proto` и не ограничивает время ответа.

```bash
sudo apt-get install -y caddy
sudo cp /opt/mfa-gate/deploy/proxy/Caddyfile /etc/caddy/Caddyfile
sudo editor /etc/caddy/Caddyfile
```

Замените имена, сети в `remote_ip` и пути к сертификату внутреннего имени в строке `tls`. Для одного адреса удалите блок `auth.example.org`, а в блоке `mfa.example.org` удалите строку `tls …`, если имя видно из Интернета: тогда Caddy сам получит для него сертификат Let's Encrypt.

```bash
sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

**Что увидите:** `Valid configuration`. Если пакета `caddy` нет в репозитории вашей ОС, подключите официальный репозиторий по инструкции <https://caddyserver.com/docs/install>.

### Шаг 3. Настройте MFA Gate

```bash
cd /opt/mfa-gate/deploy
sudo sed -i -e 's|^MFA_GATE_PUBLIC_URL=.*|MFA_GATE_PUBLIC_URL=https://mfa.example.org|' \
            -e 's|^MFA_GATE_HTTP_BIND=.*|MFA_GATE_HTTP_BIND=127.0.0.1|' .env
```

Для двух адресов задайте внешний адрес. Строка `MFA_GATE_DEVICE_URL` есть в `.env` новых установок; на установках прежних версий её может не быть, поэтому команда сначала добавляет её:

```bash
sudo grep -q '^MFA_GATE_DEVICE_URL=' .env || echo 'MFA_GATE_DEVICE_URL=' | sudo tee -a .env >/dev/null
sudo sed -i 's|^MFA_GATE_DEVICE_URL=.*|MFA_GATE_DEVICE_URL=https://auth.example.org|' .env
```

Примените:

```bash
sudo grep -E '^MFA_GATE_(PUBLIC_URL|DEVICE_URL|HTTP_BIND|HTTP_PORT|TRUSTED_PROXIES)=' .env
sudo mfagatectl start
```

**Что увидите:** заданные значения и `==> запущено`. Команда `start` пересоздаёт контейнеры с новыми настройками; `restart` для этого не подходит.

**Прокси на другом сервере**, например `198.51.100.30` в DMZ: добавьте его адрес к `auto` и укажите в `MFA_GATE_HTTP_BIND` адрес интерфейса, к которому он подключается. Целые сети в `MFA_GATE_TRUSTED_PROXIES` не указывайте.

```bash
sudo sed -i -e 's|^MFA_GATE_TRUSTED_PROXIES=.*|MFA_GATE_TRUSTED_PROXIES=auto,198.51.100.30/32|' \
            -e 's|^MFA_GATE_HTTP_BIND=.*|MFA_GATE_HTTP_BIND=192.0.2.10|' /opt/mfa-gate/deploy/.env
sudo mfagatectl start
```

Тогда закройте порт 8080 для всех, кроме прокси: [Работа по HTTP](Работа-по-HTTP.md), раздел про межсетевой экран.

### Шаг 4. Проверьте

С сервера или из внутренней сети:

```bash
curl -fsS https://mfa.example.org/health/ready; echo
curl -m 5 -sS http://192.0.2.10:8080/health/live || echo "прямой доступ закрыт — так и нужно"
sudo mfagatectl diagnose | grep -E 'Адрес (портала|для телефонов)'
```

**Что увидите:** JSON со `"status":"ok"`; `прямой доступ закрыт — так и нужно`; строки `OK   Адрес портала: https://mfa.example.org` и `OK   Адрес для телефонов: https://auth.example.org`. При одном адресе вторая строка — `OK   Адрес для телефонов: https://mfa.example.org — тот же, что у портала` с подсказкой про внешний адрес.

Для двух адресов — с компьютера вне вашей сети или с телефона в мобильной сети:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://auth.example.org/admin/
curl -s -o /dev/null -w '%{http_code}\n' https://auth.example.org/api/v1/
curl -s https://auth.example.org/api/device/v1/requests; echo
curl -m 5 -s -o /dev/null -w '%{http_code}\n' https://mfa.example.org/admin/ || echo "внутреннее имя снаружи недоступно — так и нужно"
```

**Что увидите:** `404`, `404`, JSON-ошибку MFA Gate (запрос без учётных данных устройства — значит, Device API доступен), затем `403` или `внутреннее имя снаружи недоступно — так и нужно`.

В браузере откройте `https://mfa.example.org/admin/`, войдите и откройте **Панель → Журнал**. В событии входа должен быть **ваш** адрес, а не `127.0.0.1` и не адрес вида `172.x.x.x`. Если видите адрес прокси, его нет в `MFA_GATE_TRUSTED_PROXIES`.

### Шаг 5. Перепривяжите телефоны и обновите интеграции

- **Телефоны**, привязанные по старому адресу, ходят на него и после перехода. Адрес сервера телефон берёт из QR-кода, поэтому после смены `MFA_GATE_PUBLIC_URL` или `MFA_GATE_DEVICE_URL` выпустите новый QR-код (**Панель → Пользователи → карточка → Привязать телефон**) или попросите сотрудника привязать телефон в личном кабинете. Старое устройство отзовите.
- **REST API и скрипт OpenVPN** — замените адрес на `https://mfa.example.org`. На внешнем имени REST API нет.
- **RADIUS** не меняется: он идёт по UDP мимо прокси.

## Что получится

- Кабинет и панель открываются по HTTPS без предупреждений браузера; HTTP перенаправляется на HTTPS.
- Порт 8080 доступен только прокси.
- Снаружи доступен только Device API на внешнем имени.
- В журнале видны настоящие адреса клиентов.

## Если не получается

| Что видно | Почему | Что сделать |
|---|---|---|
| `unknown directive "http2"` в `nginx -t` | nginx старше 1.25.1 | удалите `http2 on;`, пишите `listen 443 ssl http2;` |
| `unknown directive "ssl_reject_handshake"` | nginx старше 1.19.4 | удалите этот блок |
| `502 Bad Gateway` | MFA Gate не слушает адрес из `proxy_pass` | сверьте порт с `MFA_GATE_HTTP_PORT`, `sudo mfagatectl status` |
| В журнале адрес прокси вместо адреса клиента | прокси не входит в доверенные | добавьте его в `MFA_GATE_TRUSTED_PROXIES`, `sudo mfagatectl start` |
| Телефон не привязывается к `https://auth.example.org` | сертификат не от публичного УЦ или неполная цепочка | используйте Let's Encrypt и `fullchain.pem` |
| Подтверждение с телефона обрывается | таймаут чтения прокси меньше 40 с | `proxy_read_timeout 75s;` и `proxy_buffering off;` |
| `https://auth.example.org/admin/` открывает панель | `Host` не передаётся или имя внешнего адреса совпадает с внутренним | `proxy_set_header Host $host;`, проверьте `MFA_GATE_DEVICE_URL` |

## Что дальше

- [Сценарий: переход на HTTPS и внешний адрес](Сценарий-Переход-на-HTTPS-и-внешний-адрес.md)
- [Адреса и публикация](Адреса-и-публикация.md)
- [Справочник: порты и сеть](Справочник-порты-и-сеть.md)
