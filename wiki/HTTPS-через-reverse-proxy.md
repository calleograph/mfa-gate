# HTTPS через reverse proxy

MFA Gate не завершает TLS сам. HTTPS даёт reverse proxy (nginx или Caddy) на том же сервере: он принимает соединения на 443 и передаёт их в MFA Gate на `127.0.0.1:8080`. Нужно DNS-имя, например `mfa.example.org`, указывающее на сервер.

Через прокси публикуются личный кабинет сотрудника (`/`), панель администратора (`/admin/`), REST API (`/api/v1/`), Device API для телефонов (`/api/device/v1/`), статика (`/assets/`) и `/health/*`. RADIUS (UDP 1812) через прокси не идёт и не меняется.

## Один адрес или два

| Схема | Когда | Настройки |
|---|---|---|
| **Один адрес** `mfa.example.org` | телефоны подключаются к офисной сети (Wi-Fi), пилот, всё внутри | `MFA_GATE_PUBLIC_URL=https://mfa.example.org`; `MFA_GATE_DEVICE_URL` не задан |
| **Два адреса**: внутренний `mfa.example.org` и внешний `auth.example.org` | сотрудники входят в VPN из дома: телефон подтверждает вход до подключения VPN и должен достучаться до сервера из Интернета | `MFA_GATE_PUBLIC_URL=https://mfa.example.org`, `MFA_GATE_DEVICE_URL=https://auth.example.org` |

Во второй схеме внешний адрес публикует **только** `/api/device/`, всё остальное — 404. Кабинет, панель и REST API остаются во внутренней сети. Если имена в двух адресах различаются, MFA Gate и сам отвечает 404 на любой запрос к внешнему имени вне `/api/device/` и `/health/` — на случай ошибки в прокси. Зачем и как устроено: [Сетевая схема и публикация](Сетевая-схема-и-публикация.md).

Ниже конфигурации для одного адреса; для двух — готовые файлы `deploy/proxy/nginx.conf` и `deploy/proxy/Caddyfile` (раздел «Два адреса»).

Перед началом проверьте, что имя указывает на сервер и порты 80/443 свободны:

```bash
getent hosts mfa.example.org; sudo ss -ltnp '( sport = :80 or sport = :443 )'
```

**Ожидаемый результат:** IP сервера `192.0.2.10`; вторая команда печатает только заголовок (или ваш уже работающий nginx — тогда добавьте в него только `server` из варианта A).

## Что важно для MFA Gate

| Требование | Почему |
|---|---|
| Прокси **перезаписывает** `X-Forwarded-For` адресом клиента | MFA Gate берёт адрес из последнего значения заголовка; адрес нужен для журнала, ограничений ключей API и лимита попыток входа |
| Прокси передаёт `X-Forwarded-Proto: https` | по нему MFA Gate ставит cookie панели флаг `Secure` (`MFA_GATE_COOKIE_SECURE=auto`) |
| Адрес прокси входит в `MFA_GATE_TRUSTED_PROXIES` | заголовки принимаются только от доверенных адресов. Значение по умолчанию `auto` — loopback и шлюз сети контейнера: так доверенным оказывается прокси на этом же сервере, а узлы локальной сети — нет |
| Таймаут чтения у прокси ≥ 60 с | телефоны держат запрос открытым до 30 секунд (long-poll) |
| `MFA_GATE_HTTP_BIND=127.0.0.1` | после настройки порт 8080 не должен быть доступен снаружи напрямую, в обход TLS |
| Прокси передаёт исходный `Host` | по нему MFA Gate отличает внешнее имя Device API |
| `MFA_GATE_PUBLIC_URL=https://mfa.example.org` | адрес кабинета и панели; попадает в QR-коды привязки, если не задан `MFA_GATE_DEVICE_URL` |
| `MFA_GATE_DEVICE_URL=https://auth.example.org` (только для двух адресов) | внешний адрес Device API; попадает в QR-коды привязки телефонов |

## Вариант A. nginx + Let's Encrypt

### Шаг 1. Установка

```bash
sudo apt-get install -y nginx certbot python3-certbot-nginx
```

### Шаг 2. Конфигурация

```bash
sudo tee /etc/nginx/sites-available/mfa-gate >/dev/null <<'EOF'
server {
    listen 80;
    server_name mfa.example.org;
    location / { return 301 https://$host$request_uri; }
}

server {
    listen 443 ssl;
    http2 on;
    server_name mfa.example.org;

    ssl_certificate     /etc/letsencrypt/live/mfa.example.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mfa.example.org/privkey.pem;

    client_max_body_size 1m;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;   # перезаписать, не дописывать к значению клиента
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 75s;                          # long-poll телефонов до 30 с
        proxy_buffering off;
    }
}
EOF
```

Директива `http2 on` есть в nginx 1.25.1 и новее. В Ubuntu 22.04 (nginx 1.18) удалите эту строку и напишите `listen 443 ssl http2;`.

### Шаг 3. Сертификат и включение

Сертификата ещё нет, поэтому получите его, пока сайт не включён:

```bash
sudo certbot certonly --nginx -d mfa.example.org
sudo ln -s /etc/nginx/sites-available/mfa-gate /etc/nginx/sites-enabled/mfa-gate
sudo nginx -t && sudo systemctl reload nginx
```

**Ожидаемый результат:** `Successfully received certificate`, затем `syntax is ok` и `test is successful`.

Продление certbot настраивает сам (таймер `certbot.timer`). Проверка: `sudo certbot renew --dry-run`.

## Вариант B. Caddy

Caddy сам получает и продлевает сертификат, сам ставит `X-Forwarded-For` (входящему заголовку не доверяет) и `X-Forwarded-Proto`.

```bash
sudo apt-get install -y caddy
sudo tee /etc/caddy/Caddyfile >/dev/null <<'EOF'
mfa.example.org {
    reverse_proxy 127.0.0.1:8080
}
EOF
sudo systemctl reload caddy
```

**Ожидаемый результат:** `systemctl status caddy` — `active (running)`; в `journalctl -u caddy` — `certificate obtained successfully`.

Если пакета `caddy` нет в репозитории вашей ОС, подключите официальный репозиторий по инструкции <https://caddyserver.com/docs/install>.

## Два адреса

Файлы в репозитории (каталог установки `/opt/mfa-gate`):

- `deploy/proxy/nginx.conf` — внутреннее имя со всем приложением, ограниченное по адресам источника (`allow`/`deny`); внешнее имя только с `/api/device/`; неизвестные имена отклоняются;
- `deploy/proxy/Caddyfile` — то же для Caddy.

### nginx

```bash
sudo certbot certonly --nginx -d auth.example.org
sudo cp /opt/mfa-gate/deploy/proxy/nginx.conf /etc/nginx/sites-available/mfa-gate
sudo editor /etc/nginx/sites-available/mfa-gate
```

В файле замените:

1. имена `mfa.example.org` и `auth.example.org` своими;
2. сети в `allow` — на внутренние сети и пул адресов VPN;
3. пути к сертификатам: для внешнего имени — `/etc/letsencrypt/live/auth.example.org/…` из первой команды; для внутреннего — сертификат корпоративного УЦ или Let's Encrypt с проверкой DNS-01 (по HTTP имя, которого не видно из Интернета, не проверить).

```bash
sudo ln -sf /etc/nginx/sites-available/mfa-gate /etc/nginx/sites-enabled/mfa-gate
sudo nginx -t && sudo systemctl reload nginx
```

**Ожидаемый результат:** `syntax is ok`, `test is successful`.

Если на сервере уже есть сайт с `default_server` на порту 443 или 80, удалите из файла соответствующий блок `default_server` — nginx не допускает двух.

### Caddy

```bash
sudo cp /opt/mfa-gate/deploy/proxy/Caddyfile /etc/caddy/Caddyfile
sudo editor /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

Замените имена, сети в `remote_ip` и пути к сертификату внутреннего имени. Для внешнего имени Caddy получит сертификат Let's Encrypt сам.

**Ожидаемый результат:** `Valid configuration`.

### Периметр

На внешнем межсетевом экране пробросьте `auth.example.org:443` (и `:80`, если сертификат выпускается по HTTP-01) на прокси с сохранением адреса клиента. Внутреннее имя наружу не публикуйте.

## Шаг 4. Настройки MFA Gate

```bash
cd /opt/mfa-gate/deploy
sudo sed -i -e 's|^MFA_GATE_PUBLIC_URL=.*|MFA_GATE_PUBLIC_URL=https://mfa.example.org|' \
            -e 's|^MFA_GATE_HTTP_BIND=.*|MFA_GATE_HTTP_BIND=127.0.0.1|' .env
sudo grep -E '^MFA_GATE_(PUBLIC_URL|HTTP_BIND|HTTP_PORT|TRUSTED_PROXIES)=' .env
sudo mfagatectl start
```

Для двух адресов добавьте внешний адрес Device API:

```bash
cd /opt/mfa-gate/deploy
sudo grep -q '^MFA_GATE_DEVICE_URL=' .env || echo 'MFA_GATE_DEVICE_URL=' | sudo tee -a .env >/dev/null
sudo sed -i 's|^MFA_GATE_DEVICE_URL=.*|MFA_GATE_DEVICE_URL=https://auth.example.org|' .env
sudo mfagatectl start
```

**Ожидаемый результат:**

```text
MFA_GATE_PUBLIC_URL=https://mfa.example.org
MFA_GATE_HTTP_BIND=127.0.0.1
MFA_GATE_HTTP_PORT=8080
MFA_GATE_TRUSTED_PROXIES=auto
```

и `запущено`. Если `MFA_GATE_HTTP_PORT` не 8080, поправьте `proxy_pass` / `reverse_proxy`.

Если прокси стоит **на другом сервере** (например, `198.51.100.30`), добавьте его адрес к `auto` и укажите в `MFA_GATE_HTTP_BIND` адрес интерфейса, к которому он подключается:

```bash
sudo sed -i -e 's|^MFA_GATE_TRUSTED_PROXIES=.*|MFA_GATE_TRUSTED_PROXIES=auto,198.51.100.30/32|' \
            -e 's|^MFA_GATE_HTTP_BIND=.*|MFA_GATE_HTTP_BIND=192.0.2.10|' /opt/mfa-gate/deploy/.env
sudo mfagatectl start
```

Тогда закройте порт 8080 для всех, кроме прокси (цепочка `DOCKER-USER`, пример — [Работа по HTTP](Работа-по-HTTP.md)).

## Шаг 5. Проверка

```bash
curl -fsS https://mfa.example.org/health/ready; echo
curl -sI http://mfa.example.org/ | head -n1
curl -m 5 -sS http://192.0.2.10:8080/health/live || echo "прямой доступ закрыт — так и нужно"
sudo mfagatectl diagnose | grep 'Публичный адрес'
```

**Ожидаемый результат:**

1. JSON со `"status":"ok"`;
2. `HTTP/1.1 301 Moved Permanently` (для nginx; Caddy отвечает `308`);
3. `прямой доступ закрыт — так и нужно`;
4. `OK   Публичный адрес: https://mfa.example.org`.

Для двух адресов — с компьютера **вне** вашей сети (или с телефона в мобильной сети):

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://auth.example.org/admin/
curl -s -o /dev/null -w '%{http_code}\n' https://auth.example.org/api/v1/auth/me
curl -s https://auth.example.org/api/device/v1/requests; echo
curl -m 5 -s -o /dev/null -w '%{http_code}\n' https://mfa.example.org/admin/ || echo "внутреннее имя снаружи недоступно — так и нужно"
```

**Ожидаемый результат:** `404`, `404`, JSON-ошибка MFA Gate (запрос без учётных данных устройства — значит, Device API доступен), затем `403` или `внутреннее имя снаружи недоступно — так и нужно`.

В браузере: `https://mfa.example.org/admin/` открывает панель, `https://mfa.example.org/` — личный кабинет сотрудника, замок без предупреждений. Войдите и откройте «Журнал»: в событии входа должен быть **ваш** IP, а не `127.0.0.1` или `172.x.x.x`. Если видите адрес прокси — `MFA_GATE_TRUSTED_PROXIES` не содержит адрес, с которого прокси подключается.

## Шаг 6. Перепривязка телефонов и шлюзов

- **Телефоны**, привязанные по `http://`, работают со старым адресом, который после шага 4 недоступен. То же после появления или смены `MFA_GATE_DEVICE_URL`: адрес сервера телефон берёт из QR-кода. Выпустите каждому сотруднику новый QR-код (или попросите добавить телефон в личном кабинете) и привяжите телефон заново; старое устройство отзовите.
- **REST API и скрипт OpenVPN**: замените адрес на `https://mfa.example.org` (внутренний; на внешнем имени REST API нет).
- **RADIUS** не меняется.
