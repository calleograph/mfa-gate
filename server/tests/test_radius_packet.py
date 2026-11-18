"""Кодек RADIUS: тестовые векторы RFC 2865 §7.1 и проверка Message-Authenticator."""
import pytest

from mfa_gate.radius import packet as rp

SECRET = b"xyzzy5461"
# RFC 2865 §7.1: Access-Request от NAS 192.168.1.16, пользователь nemo, пароль arctangent
RFC_REQUEST = bytes.fromhex(
    "0100003 80f403f9473978057bd83d5cb98f4227a01066e656d6f02120dbe708d93d413ce3196e43f782a0aee0406c0a8011005060000 0003".replace(" ", ""))
RFC_ACCEPT_AUTH = bytes.fromhex("86fe220e7624ba2a1005f6bf9b55e0b2")


def test_rfc_request_decodes_and_password_decrypts():
    pkt = rp.decode(RFC_REQUEST)
    assert pkt.code == rp.ACCESS_REQUEST and pkt.identifier == 0
    assert pkt.text(rp.USER_NAME) == "nemo"
    assert rp.decrypt_password(pkt.get(rp.USER_PASSWORD), SECRET, pkt.authenticator) == "arctangent"


def test_password_roundtrip_long_and_unicode():
    auth = bytes(range(16))
    for pw in ["a", "x" * 16, "пароль-с-юникодом-длиннее-16", "y" * 100]:
        assert rp.decrypt_password(rp.encrypt_password(pw, SECRET, auth), SECRET, auth) == pw


def test_rfc_accept_response_authenticator():
    # RFC 2865 §7.1: Access-Accept с Service-Type=Login, Login-Service=Telnet, Login-IP-Host=192.168.1.3 (без Message-Authenticator)
    import hashlib
    req = rp.decode(RFC_REQUEST)
    attrs = bytes.fromhex("06060000000 10f06000000000e06c0a80103".replace(" ", ""))
    header = bytes([2, req.identifier]) + (20 + len(attrs)).to_bytes(2, "big")
    assert hashlib.md5(header + req.authenticator + attrs + SECRET).digest() == RFC_ACCEPT_AUTH


def test_request_message_authenticator_roundtrip_and_tamper():
    data = rp.encode_request(7, SECRET, [(rp.USER_NAME, b"alice"), (rp.USER_PASSWORD, b"secret")])
    pkt = rp.decode(data)
    assert rp.verify_message_authenticator(data, pkt, SECRET) is True
    assert rp.verify_message_authenticator(data, pkt, b"wrong") is False
    tampered = data[:-1] + bytes([data[-1] ^ 1])
    assert rp.verify_message_authenticator(tampered, rp.decode(tampered), SECRET) is False
    plain = rp.encode_request(8, SECRET, [(rp.USER_NAME, b"alice")], message_authenticator=False)
    assert rp.verify_message_authenticator(plain, rp.decode(plain), SECRET) is None


def test_response_is_verifiable_by_client():
    req_data = rp.encode_request(9, SECRET, [(rp.USER_NAME, b"alice"), (rp.USER_PASSWORD, b"pw")])
    req = rp.decode(req_data)
    resp = rp.encode_response(rp.ACCESS_CHALLENGE, req, SECRET, [(rp.STATE, b"s" * 16), (rp.REPLY_MESSAGE, "Код".encode())])
    assert rp.verify_response(resp, req.authenticator, SECRET)
    parsed = rp.decode(resp)
    assert parsed.code == rp.ACCESS_CHALLENGE and parsed.identifier == 9 and parsed.get(rp.STATE) == b"s" * 16
    # Message-Authenticator ответа считается по Request Authenticator (RFC 3579 §3.2)
    import hashlib
    import hmac
    zeroed = resp[:4] + req.authenticator + resp[20:22] + b"\x00" * 16 + resp[38:]
    assert hmac.new(SECRET, zeroed, hashlib.md5).digest() == parsed.get(rp.MESSAGE_AUTHENTICATOR)


@pytest.mark.parametrize("data", [b"", b"\x01" * 19, bytes.fromhex("0100ffff") + b"\x00" * 16,
                                  bytes.fromhex("01000016") + b"\x00" * 16 + b"\x01\x01"])
def test_malformed_packets_rejected(data):
    with pytest.raises(rp.PacketError):
        rp.decode(data)
