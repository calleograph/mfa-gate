"""Карта адресов для экрана «Адреса и публикация»: что куда опубликовать на reverse proxy и межсетевом экране (docs/network.md)."""
from __future__ import annotations

from urllib.parse import urlsplit

from ..config import Settings

PUSH_HOSTS = {"fcm": ["fcm.googleapis.com", "oauth2.googleapis.com"], "hms": ["push-api.cloud.huawei.com", "oauth-login.cloud.huawei.com"]}


def _upstream(published: str) -> str:
    host, _, port = published.rpartition(":")
    return f"{'127.0.0.1' if host in ('', '0.0.0.0', '::') else host}:{port or '8080'}"   # noqa: S104 — сравнение, не привязка


def build(cfg: Settings, radius_clients: list[tuple[str, str, str]], directories: list[tuple[str, list[str], int, str]],
          providers: list[str]) -> dict:
    """radius_clients: (имя, адрес, first_factor); directories: (имя, хосты, порт, шифрование); providers: включённые push-провайдеры."""
    portal, device = cfg.public_url.rstrip("/"), cfg.device_url.rstrip("/")
    upstream = _upstream(cfg.published_http or "0.0.0.0:8080")
    radius_port = (cfg.published_radius or ":1812").rpartition(":")[2] or "1812"
    gateways = ", ".join(f"{n} ({a})" for n, a, _ in radius_clients) or "шлюзы ещё не добавлены"
    https = "HTTPS 443" if portal.startswith("https://") else "HTTP (только изолированная сеть)"
    items = [
        {"id": "portal", "name": "Личный кабинет сотрудника", "purpose": "Вход доменной учётной записью, настройка второго фактора",
         "address": portal + "/", "protocol": https, "direction": "входящее", "access": "внутренняя сеть компании",
         "proxy": "да: TLS на прокси, всё кроме /api/device/ — только изнутри", "upstream": upstream},
        {"id": "admin", "name": "Панель управления", "purpose": "Администрирование MFA Gate",
         "address": portal + "/admin/", "protocol": https, "direction": "входящее", "access": "администраторы; ограничьте по адресам на прокси",
         "proxy": "тот же сервер прокси, что и портал", "upstream": upstream},
        {"id": "device", "name": "Device API", "purpose": "Привязка телефонов и подтверждение входов из дома, до подключения VPN",
         "address": device + "/api/device/v1/", "protocol": "HTTPS 443" if device.startswith("https://") else "HTTP — телефоны предупредят о риске",
         "direction": "входящее из интернета", "access": "телефоны сотрудников (внешняя сеть)",
         "proxy": "да: наружу публикуется только префикс /api/device/, таймаут чтения ≥ 40 с (long-poll)", "upstream": upstream},
        {"id": "api", "name": "REST API", "purpose": "Интеграции: OpenVPN-скрипт, приложения, свои сервисы",
         "address": portal + "/api/v1/", "protocol": https, "direction": "входящее", "access": "серверы-интеграции; ключи API с разрешёнными адресами",
         "proxy": "тот же, что и портал", "upstream": upstream},
        {"id": "radius", "name": "RADIUS", "purpose": "Проверка входа для VPN-шлюзов (PAP, Access-Challenge, ожидание push)",
         "address": f"UDP {radius_port}", "protocol": "RADIUS (RFC 2865) + Message-Authenticator", "direction": "входящее",
         "access": "только VPN-шлюзы: " + gateways, "proxy": "нет: разрешите на межсетевом экране только адреса шлюзов; между площадками — IPsec/WireGuard",
         "upstream": cfg.published_radius or f"0.0.0.0:{radius_port}"},
        {"id": "accounting", "name": "RADIUS Accounting", "purpose": "Учёт VPN-сессий", "address": "UDP 1813", "protocol": "RADIUS (RFC 2866)",
         "direction": "входящее", "access": "VPN-шлюзы", "proxy": "нет", "upstream": "", "planned": True},
    ]
    for name, hosts, port, enc in directories:
        items.append({"id": "ldap", "name": f"Каталог «{name}»", "purpose": "Проверка доменных учётных записей и групп",
                      "address": ", ".join(f"{h}:{port}" for h in hosts), "protocol": {"ldaps": "LDAPS", "starttls": "LDAP + STARTTLS"}.get(enc, "LDAP без шифрования"),
                      "direction": "исходящее", "access": "сервер MFA Gate → контроллеры домена", "proxy": "нет", "upstream": ""})
    for provider in providers:
        hosts = PUSH_HOSTS.get(provider) or [h.strip() for h in cfg.push_unifiedpush_hosts.split(",") if h.strip()]
        items.append({"id": "push", "name": f"Доставка push ({provider.upper() if provider != 'unifiedpush' else 'UnifiedPush'})",
                      "purpose": "Сигнал телефону о новом запросе", "address": ", ".join(f"{h}:443" for h in hosts), "protocol": "HTTPS",
                      "direction": "исходящее", "access": "сервер MFA Gate → сервис доставки", "proxy": "нет", "upstream": ""})

    warnings = []
    if device == portal:
        warnings.append("Телефоны ходят на внутренний адрес: из дома, до подключения VPN, они его не увидят. Задайте MFA_GATE_DEVICE_URL — "
                        "внешний HTTPS-адрес, опубликованный на прокси только для /api/device/.")
    if not device.startswith("https://"):
        warnings.append("Device API без HTTPS: в интернет так публиковать нельзя.")
    if not cfg.portal_enroll_networks.strip():
        warnings.append("Первичная настройка фактора разрешена с любого адреса (MFA_GATE_PORTAL_ENROLL_NETWORKS пуст).")
    if any(ff == "gateway" for _, _, ff in radius_clients):
        warnings.append("Есть RADIUS-клиенты в режиме «пароль проверяет шлюз»: MFA Gate доверяет им проверку пароля — держите их секреты и адреса под контролем.")
    return {"components": items, "warnings": warnings, "push_timeout": cfg.push_timeout,
            "gateway_timeout": max(60, cfg.push_timeout + 15), "nginx": _nginx(portal, device, upstream), "caddy": _caddy(portal, device, upstream)}


def _host(url: str) -> str:
    return urlsplit(url).hostname or "mfa.example.org"


def _nginx(portal: str, device: str, upstream: str) -> str:
    common = ("        proxy_set_header Host $host;\n        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
              "        proxy_set_header X-Forwarded-Proto $scheme;\n")
    out = (f"# внутренний адрес: портал, панель, REST API — только из сетей компании\nserver {{\n    listen 443 ssl;\n    server_name {_host(portal)};\n"
           f"    ssl_certificate     /etc/ssl/mfa/fullchain.pem;\n    ssl_certificate_key /etc/ssl/mfa/privkey.pem;\n"
           f"    allow 10.0.0.0/8;\n    allow 172.16.0.0/12;\n    allow 192.168.0.0/16;\n    deny all;\n"
           f"    location / {{\n        proxy_pass http://{upstream};\n{common}        proxy_read_timeout 60s;\n    }}\n}}\n")
    if _host(device) != _host(portal):
        out += (f"\n# внешний адрес: только Device API для телефонов\nserver {{\n    listen 443 ssl;\n    server_name {_host(device)};\n"
                f"    ssl_certificate     /etc/ssl/auth/fullchain.pem;\n    ssl_certificate_key /etc/ssl/auth/privkey.pem;\n"
                f"    location /api/device/ {{\n        proxy_pass http://{upstream};\n{common}        proxy_read_timeout 60s;\n    }}\n"
                f"    location / {{ return 404; }}\n}}\n")
    return out


def _caddy(portal: str, device: str, upstream: str) -> str:
    out = (f"{_host(portal)} {{\n\t@outside not remote_ip private_ranges\n\trespond @outside 403\n"
           f"\treverse_proxy {upstream}\n}}\n")
    if _host(device) != _host(portal):
        out += f"\n{_host(device)} {{\n\thandle /api/device/* {{\n\t\treverse_proxy {upstream}\n\t}}\n\trespond 404\n}}\n"
    return out
