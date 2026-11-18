# HTTPS через reverse proxy

MFA Gate не завершает TLS сам. HTTPS даёт reverse proxy (nginx или Caddy) на том же сервере: он принимает соединения на 443 и передаёт их в MFA Gate на `127.0.0.1:8080`. Нужно DNS-имя, например `mfa.example.org`, указывающее на сервер.

Через прокси публикуются панель (`/`), REST API (`/api/v1/`), Device API для телефонов (`/api/device/v1/`) и `/health/*`. RADIUS (UDP 1812) через прокси не идёт и не меняется.

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
| `MFA_GATE_PUBLIC_URL=https://mfa.example.org` | этот адрес попадает в QR-коды привязки телефонов |

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

## Шаг 4. Настройки MFA Gate

```bash
cd /opt/mfa-gate/deploy
sudo sed -i -e 's|^MFA_GATE_PUBLIC_URL=.*|MFA_GATE_PUBLIC_URL=https://mfa.example.org|' \
            -e 's|^MFA_GATE_HTTP_BIND=.*|MFA_GATE_HTTP_BIND=127.0.0.1|' .env
sudo grep -E '^MFA_GATE_(PUBLIC_URL|HTTP_BIND|HTTP_PORT|TRUSTED_PROXIES)=' .env
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

В браузере: `https://mfa.example.org` открывает панель, замок без предупреждений. Войдите и откройте «Журнал»: в событии входа должен быть **ваш** IP, а не `127.0.0.1` или `172.x.x.x`. Если видите адрес прокси — `MFA_GATE_TRUSTED_PROXIES` не содержит адрес, с которого прокси подключается.

## Шаг 6. Перепривязка телефонов и шлюзов

- **Телефоны**, привязанные по `http://`, работают со старым адресом, который после шага 4 недоступен. Выпустите каждому сотруднику новый QR-код и привяжите телефон заново; старое устройство отзовите.
- **REST API и скрипт OpenVPN**: замените адрес на `https://mfa.example.org`.
- **RADIUS** не меняется.
