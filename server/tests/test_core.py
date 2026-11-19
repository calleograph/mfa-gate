"""Логика без БД: политики, маскирование журнала, подпись устройства, шифрование с AAD."""
import base64
import os
import uuid

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from mfa_gate.models import Group, Policy
from mfa_gate.security import crypto
from mfa_gate.security.secretbox import SecretBox, SecretBoxError
from mfa_gate.services.audit import scrub
from mfa_gate.services.policy import effective_groups, evaluate

R1, R2 = uuid.uuid4(), uuid.uuid4()


def policy(**kw) -> Policy:
    base = {"id": uuid.uuid4(), "name": "p", "priority": 100, "enabled": True, "resource_ids": [], "groups": [], "exclude_groups": [],
            "action": "mfa", "methods": ["push", "totp"], "on_unavailable": "deny"}
    return Policy(**{**base, **kw})


def test_no_policy_means_mfa_required():
    d = evaluate([], R1, {"staff"})
    assert d.action == "mfa" and d.methods == ["push", "totp"] and d.on_unavailable == "deny"


def test_first_match_by_priority_and_scope():
    deny_r2 = policy(priority=1, resource_ids=[str(R2)], action="deny")
    allow_admins = policy(priority=5, groups=["Admins"], action="allow")
    totp_all = policy(priority=10, methods=["totp"])
    rules = [totp_all, allow_admins, deny_r2]
    assert evaluate(rules, R2, set()).action == "deny"
    assert evaluate(rules, R1, {"admins"}).action == "allow"          # группы без учёта регистра
    assert evaluate(rules, R1, {"staff"}).methods == ["totp"]
    assert evaluate([policy(enabled=False, action="deny")], R1, set()).action == "mfa"


def test_exclude_groups_and_unknown_methods():
    rule = policy(groups=["vpn"], exclude_groups=["contractors"], action="allow")
    assert evaluate([rule], R1, {"vpn"}).action == "allow"
    assert evaluate([rule], R1, {"vpn", "contractors"}).action == "mfa"
    assert evaluate([policy(methods=["sms"])], R1, set()).methods == ["push", "totp"]


def test_effective_groups_maps_directory_groups():
    local = [Group(name="Local")]
    mapped = [Group(name="VPN users", directory_group="CN=VPN,OU=Groups,DC=corp,DC=example,DC=org"), Group(name="Other", directory_group="x")]
    names = effective_groups(local, ["CN=VPN,OU=Groups,DC=corp,DC=example,DC=org", "VPN"], mapped)
    assert names == {"local", "vpn users", "cn=vpn,ou=groups,dc=corp,dc=example,dc=org", "vpn"}


def test_scrub_hides_secret_like_keys_recursively():
    out = scrub({"login": "a", "password": "p", "nested": {"api_secret": "s", "items": [{"otp": "1"}]}, "challenge": "c", "reason": "r"})
    assert out == {"login": "a", "password": "***", "nested": {"api_secret": "***", "items": [{"otp": "***"}]}, "challenge": "***", "reason": "r"}


def _device():
    key = ec.generate_private_key(ec.SECP256R1())
    spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return key, crypto.b64url(spki)


def test_decision_signature_roundtrip_and_tamper():
    key, pub = _device()
    msg = crypto.decision_message("req", "dev", "chal", "approve")
    sig = crypto.b64url(key.sign(msg, ec.ECDSA(hashes.SHA256())))
    assert crypto.verify_decision(pub, msg, sig)
    assert not crypto.verify_decision(pub, crypto.decision_message("req", "dev", "chal", "deny"), sig)
    assert not crypto.verify_decision(pub, msg, crypto.b64url(b"\x30\x00"))
    _, other = _device()
    assert not crypto.verify_decision(other, msg, sig)


def test_only_p256_device_keys_accepted():
    rsa_spki = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    with pytest.raises(ValueError):
        crypto.load_device_key(crypto.b64url(rsa_spki))
    p384 = ec.generate_private_key(ec.SECP384R1()).public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    with pytest.raises(ValueError):
        crypto.load_device_key(crypto.b64url(p384))


def test_secretbox_aad_binds_ciphertext_to_record():
    box = SecretBox(base64.b64encode(os.urandom(32)).decode())
    token = box.encrypt("JBSWY3DPEHPK3PXP", aad=b"totp:1")
    assert box.decrypt(token, aad=b"totp:1") == "JBSWY3DPEHPK3PXP"
    with pytest.raises(SecretBoxError):
        box.decrypt(token, aad=b"totp:2")
    with pytest.raises(SecretBoxError):
        SecretBox(base64.b64encode(os.urandom(16)).decode())


def test_token_hash_compare():
    token = crypto.new_token()
    assert crypto.same_hash(token, crypto.sha256_hex(token)) and not crypto.same_hash(token + "x", crypto.sha256_hex(token))
    assert crypto.verify_password("pw-123456789012", crypto.hash_password("pw-123456789012"))
    assert not crypto.verify_password("pw", None)


async def test_unifiedpush_rejects_bad_and_foreign_urls():
    import httpx

    from mfa_gate.services.push import PushError, UnifiedPushProvider
    provider = UnifiedPushProvider({"push.example.org"})
    async with httpx.AsyncClient() as http:
        for url in ("https://push.example.org:abc/x", "http://push.example.org/x", "https://internal.example.org/x", "https://push.example.org/\x00"):
            with pytest.raises(PushError) as err:
                await provider.send(http, url, "r", "d")
            assert err.value.invalid_token


def test_env_example_is_valid_shell():
    """deploy/.env.example читают и docker compose, и bash (install.sh, mfagatectl): значения с пробелами — только в кавычках."""
    import subprocess
    from pathlib import Path
    env = Path(__file__).resolve().parents[2] / "deploy" / ".env.example"
    res = subprocess.run(["bash", "-c", f'set -euo pipefail; set -a; . "{env}"; echo "$MFA_GATE_SERVER_NAME"'], capture_output=True, text=True)
    assert res.returncode == 0 and res.stderr == "", res.stderr
    assert res.stdout.strip() == "MFA Gate"


def test_default_gateway_from_proc_route():
    from mfa_gate.api.deps import default_gateway
    table = ("Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
             "eth0\t00000000\t010012AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
             "eth0\t000012AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n")
    assert default_gateway(table) == "172.18.0.1"
    assert default_gateway(table.splitlines()[0]) is None


def test_compose_database_url_matches_postgres_service():
    """Строка подключения приложения и учётная запись сервиса postgres в compose.yml должны совпадать."""
    import re
    from pathlib import Path
    compose = (Path(__file__).resolve().parents[2] / "deploy" / "compose.yml").read_text()
    user, db = re.search(r"postgresql\+asyncpg://([^:]+):\$\{POSTGRES_PASSWORD:\?\}@postgres:5432/(\S+)", compose).groups()
    assert f"POSTGRES_USER: {user}" in compose and f"POSTGRES_DB: {db}" in compose


def test_publication_map_and_proxy_snippets():
    from mfa_gate.config import Settings
    from mfa_gate.services import publication

    cfg = Settings(public_url="https://mfa.example.org", device_url="https://auth.example.org", published_http="0.0.0.0:8080",
                   published_radius="0.0.0.0:1812", push_timeout=45)
    d = publication.build(cfg, [("gw", "192.0.2.10", "gateway")], [("AD", ["dc1.example.org"], 636, "ldaps")], ["fcm"])
    by = {c["id"]: c for c in d["components"]}
    assert by["device"]["address"] == "https://auth.example.org/api/device/v1/" and by["admin"]["address"].endswith("/admin/")
    assert "gw (192.0.2.10)" in by["radius"]["access"] and by["push"]["address"].startswith("fcm.googleapis.com:443")
    assert d["gateway_timeout"] == 60 and any("шлюз" in w for w in d["warnings"])
    # внешний сервер прокси отдаёт только Device API, upstream — loopback вместо 0.0.0.0
    assert "server_name auth.example.org" in d["nginx"] and "location / { return 404; }" in d["nginx"] and "127.0.0.1:8080" in d["nginx"]
    assert "handle /api/device/*" in d["caddy"] and "remote_ip private_ranges" in d["caddy"] and "deny all;" in d["nginx"]
    same = publication.build(Settings(public_url="http://192.0.2.5:8080"), [], [], [])
    assert "auth.example.org" not in same["nginx"] and any("MFA_GATE_DEVICE_URL" in w for w in same["warnings"])


def test_portal_enroll_networks_and_backup_codes():
    from mfa_gate.api import portal

    assert portal._in_networks("10.1.2.3", "10.0.0.0/8,192.168.0.0/16")
    assert not portal._in_networks("203.0.113.9", "10.0.0.0/8")
    assert portal._in_networks("203.0.113.9", "")          # пусто — без ограничения
    assert not portal._in_networks("not-an-ip", "10.0.0.0/8")
    codes = portal.new_backup_codes()
    assert len(set(codes)) == 10 and all(len(c) == 11 and c[5] == "-" for c in codes)
    assert portal._norm_code(" ABCDE-fghjk ") == "abcdefghjk"


def test_settings_device_url_defaults_to_public():
    from mfa_gate.config import Settings

    assert Settings(public_url="http://192.0.2.5:8080/").device_url == "http://192.0.2.5:8080"
    assert Settings(public_url="http://a", device_url="https://auth.example.org/").device_url == "https://auth.example.org"
