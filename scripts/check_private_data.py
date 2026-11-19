#!/usr/bin/env python3
"""Проверка публичного репозитория на частные данные.

Ищет публичные IPv4, домены и e-mail вне списка разрешённых, закрытые ключи, токены облаков и абсолютные пути
к домашним каталогам. Разрешённое — в scripts/private-data.allow. Выход 1 при находках (файл:строка: что найдено).

Использование: scripts/check_private_data.py [--root DIR] [--self-test]
"""
from __future__ import annotations

import argparse
import fnmatch
import ipaddress
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKIP_DIRS = {".git", ".venv", "venv", "build", "node_modules", ".gradle", ".kotlin", "__pycache__", ".pytest_cache",
             ".ruff_cache", ".idea", "out"}
MAX_BYTES = 2_000_000

# Диапазоны, допустимые в публичном коде: частные (RFC 1918), loopback, link-local, CGNAT,
# документационные (RFC 5737), тестовые (RFC 2544), multicast/зарезервированные (в т.ч. маски 255.x).
SAFE_NETS = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12", "192.168.0.0/16",
    "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "198.18.0.0/15", "224.0.0.0/3")]
# Зарезервированные имена (RFC 2606/6761) разрешены всегда, в том числе для e-mail.
RESERVED_DOMAINS = ("example.org", "example.com", "example.net", "example", "test", "invalid", "localhost")

# Проверяются только эти TLD: у остальных «доменов» слишком много ложных срабатываний в коде (obj.id, file.py, a.at).
# .local/.lan/.corp/.internal/.intranet/.home — типичные внутренние зоны организаций.
# Без info/dev/group/pro/space: совпадают с атрибутами в коде (log.info, self.dev, m.group, proguard-rules.pro).
TLDS = {"com", "org", "net", "ru", "su", "xn--p1ai", "рф", "io", "biz", "co", "uk", "de", "eu", "us", "by", "kz",
        "ua", "uz", "am", "ge", "cloud", "tech", "online", "site", "xyz", "gov", "edu", "mil", "int", "cn", "jp", "fr", "nl",
        "cz", "es", "ch", "se", "fi", "no", "dk", "ca", "au", "br", "tr", "ltd",
        "local", "lan", "corp", "internal", "intranet", "home", "localdomain"}

IPV4 = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?!\d|\.\d)")
DOMAIN = re.compile(r"(?<![\w.@-])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,24}|xn--[a-z0-9]+|рф))(?![\w-]|\.\w)")
CODE_IMPORT = re.compile(r"^\s*(?:package|import)\s")   # пакеты Java/Kotlin похожи на домены (mfa_gate.app.net)
EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@((?:[\w-]+\.)+[\w-]{2,24})\b")
PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----")
TOKENS = {
    "токен GitHub": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{60,})"),
    "ключ AWS": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "ключ Google API": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "токен Slack": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "токен GitLab": re.compile(r"\bglpat-[A-Za-z0-9_-]{20}\b"),
}
# Названия продуктов, изученных при разработке: в публичных текстах их нет. Сам список тоже не публикуется — локальный файл
# scripts/reference-products-local.txt (по выражению в строке, без учёта регистра); без файла проверка пропускается.
_REF_FILE = HERE / "reference-products-local.txt"
_REF = [ln.strip() for ln in _REF_FILE.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")] \
    if _REF_FILE.exists() else []
REFERENCE_PRODUCTS = re.compile("|".join(_REF), re.IGNORECASE) if _REF else None
HOME_PATH = re.compile(r"/home/(?!<)[A-Za-z0-9._-]+|/Users/[A-Za-z0-9._-]+/|/srv/projects\b|[A-Z]:\\Users\\[A-Za-z0-9._-]+",
                       re.IGNORECASE)


class Allow:
    def __init__(self, path: Path | None):
        self.domains: set[str] = set(RESERVED_DOMAINS)
        self.emails: set[str] = set()
        self.ips: list = list(SAFE_NETS)
        self.key_files: list[str] = []
        self.skip: list[str] = []
        if path and path.exists():
            for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                line = raw.split("#", 1)[0].strip()
                if not line:
                    continue
                kind, _, value = line.partition(" ")
                value = value.strip().lower() if kind in ("domain", "email") else value.strip()
                if kind == "domain":
                    self.domains.add(value)
                elif kind == "email":
                    self.emails.add(value)
                elif kind == "ip":
                    self.ips.append(ipaddress.ip_network(value, strict=False))
                elif kind == "key-file":
                    self.key_files.append(value)
                elif kind == "skip":
                    self.skip.append(value)
                else:
                    raise SystemExit(f"{path}:{n}: неизвестная запись {kind!r} (ожидается domain|email|ip|key-file|skip)")

    def domain_ok(self, name: str) -> bool:
        name = name.lower().rstrip(".")
        return any(name == d or name.endswith("." + d) for d in self.domains)

    def email_ok(self, addr: str, domain: str) -> bool:
        domain = domain.lower()
        return addr.lower() in self.emails or any(domain == d or domain.endswith("." + d) for d in RESERVED_DOMAINS)

    def ip_ok(self, text: str) -> bool:
        try:
            ip = ipaddress.ip_address(text)
        except ValueError:
            return True   # 999.1.1.1 и подобное — не адрес (версии, OID)
        return any(ip in net for net in self.ips)

    @staticmethod
    def match(rel: str, patterns: list[str]) -> bool:
        return any(fnmatch.fnmatch(rel, p) for p in patterns)


def scan_line(line: str, allow: Allow, key_file: bool) -> list[str]:
    found: list[str] = []
    for m in IPV4.finditer(line):
        if not allow.ip_ok(m.group(1)):
            found.append(f"публичный IP {m.group(1)}")
    emails = list(EMAIL.finditer(line))
    for m in emails:
        if not allow.email_ok(m.group(0), m.group(1)):
            found.append(f"e-mail {m.group(0)}")
    email_spans = [m.span(1) for m in emails]
    for m in ([] if CODE_IMPORT.match(line) else DOMAIN.finditer(line)):
        name = m.group(1)
        if any(s <= m.start(1) < e for s, e in email_spans):
            continue
        if name.rsplit(".", 1)[-1].lower() in TLDS and not allow.domain_ok(name):
            found.append(f"домен {name}")
    if PRIVATE_KEY.search(line) and not key_file:
        found.append("закрытый ключ")
    found += [f"{label}" for label, rx in TOKENS.items() if rx.search(line)]
    found += [f"абсолютный путь {m.group(0)}" for m in HOME_PATH.finditer(line)]
    if REFERENCE_PRODUCTS:
        found += [f"название стороннего продукта {m.group(0)}" for m in REFERENCE_PRODUCTS.finditer(line)]
    return found


def list_files(root: Path) -> list[str]:
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
                             capture_output=True, check=True).stdout.decode()
        files = [f for f in out.split("\0") if f]
    except (OSError, subprocess.CalledProcessError):
        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            files += [os.path.relpath(os.path.join(dirpath, f), root) for f in filenames]
    # каталоги сборки и окружений не проверяем даже в git (страховка от неполного .gitignore)
    return sorted(f for f in files if not SKIP_DIRS.intersection(Path(f).parts[:-1]))


def scan(root: Path, allow: Allow) -> list[str]:
    report = []
    for rel in list_files(root):
        if allow.match(rel, allow.skip):
            continue
        path = root / rel
        try:
            if not path.is_file() or path.stat().st_size > MAX_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue   # двоичный файл
        key_file = allow.match(rel, allow.key_files)
        for n, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            report += [f"{rel}:{n}: {what}" for what in scan_line(line, allow, key_file)]
    return report


def self_test() -> None:
    allow = Allow(None)
    allow.domains |= {"github.com"}
    bad = {
        "server 8.8.8.8 down": "публичный IP",
        "see https://intranet.acme.ru/x": "домен",
        "mail ivan@acme.ru": "e-mail",
        "-----BEGIN RSA PRIVATE KEY-----": "закрытый ключ",
        "token ghp_" + "a" * 36: "токен GitHub",
        "key AKIA" + "A" * 16: "ключ AWS",
        "cd /home/alice/work": "абсолютный путь",
        "dc1.corp.local": "домен",
    }
    for line, expect in bad.items():
        assert any(expect in f for f in scan_line(line, allow, False)), (line, scan_line(line, allow, False))
    good = ["10.0.0.1 192.0.2.10 203.0.113.5/24 255.255.255.0 127.0.0.1", "https://mfa.example.org admin@example.com",
            "user@corp.example", "OID 1.2.840.113556.1.4.1941 and 1.3.6.1.4.1", "device.id file.py req.at self.name log.info Dispatchers.IO",
            "package org.example.app.net", "java.io.File(x)", "import mfa_gate.authenticator.net.Api",
            "https://github.com/calleograph/mfa-gate", "version 1.2.3.4567", "/home/<user>"]
    for line in good:
        assert not scan_line(line, allow, False), (line, scan_line(line, allow, False))
    print("self-test: ok")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(HERE.parent))
    parser.add_argument("--allow", default=str(HERE / "private-data.allow"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    report = scan(Path(args.root), Allow(Path(args.allow)))
    for line in report:
        print(line)
    if report:
        print(f"\nнайдено: {len(report)}. Замените на нейтральные значения или, если это публичный сервис, "
              f"добавьте в scripts/private-data.allow", file=sys.stderr)
        sys.exit(1)
    print("частных данных не найдено")


if __name__ == "__main__":
    main()
