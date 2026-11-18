#!/usr/bin/env bash
# Поднимает изолированный OpenLDAP в Docker (порты только на 127.0.0.1), засевает тестовый каталог,
# прогоняет `pytest -m ldap` и удаляет контейнер и временные файлы при любом исходе.
# Использование: tests/ldap_fixture/run.sh [аргументы pytest]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER="$(cd "$HERE/../.." && pwd)"
IMAGE="${MFA_GATE_LDAP_IMAGE:-osixia/openldap:1.5.0}"
PYTHON="${PYTHON:-$SERVER/.venv/bin/python}"
NAME="mfa-gate-test-ldap-$(openssl rand -hex 4)"
WORK="$(mktemp -d)"
BASE_DN="dc=example,dc=org"

cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

ADMIN_PW="$(openssl rand -hex 16)"
RO_PW="$(openssl rand -hex 16)"
USER_PW="$(openssl rand -hex 16)"

mkdir -p "$WORK/certs"
"$PYTHON" "$HERE/gen_certs.py" "$WORK/certs"
# готовые параметры RFC 7919: иначе образ генерирует DH 2048 бит минуты, и LDAPS всё это время не отвечает
openssl genpkey -genparam -algorithm DH -pkeyopt group:ffdhe2048 -out "$WORK/certs/dhparam.pem" 2>/dev/null
chmod 755 "$WORK" "$WORK/certs" && chmod 644 "$WORK"/certs/*   # одноразовый ключ: slapd в контейнере работает не от нашего uid

docker image inspect "$IMAGE" >/dev/null 2>&1 || docker pull "$IMAGE" >/dev/null
docker run -d --name "$NAME" \
  -p 127.0.0.1::389 -p 127.0.0.1::636 \
  -e LDAP_ORGANISATION="Example" -e LDAP_DOMAIN="example.org" \
  -e LDAP_ADMIN_PASSWORD="$ADMIN_PW" -e LDAP_CONFIG_PASSWORD="$ADMIN_PW" \
  -e LDAP_READONLY_USER=true -e LDAP_READONLY_USER_USERNAME=readonly -e LDAP_READONLY_USER_PASSWORD="$RO_PW" \
  -e LDAP_TLS=true -e LDAP_TLS_ENFORCE=false -e LDAP_TLS_VERIFY_CLIENT=never \
  -e LDAP_TLS_CRT_FILENAME=server.crt -e LDAP_TLS_KEY_FILENAME=server.key -e LDAP_TLS_CA_CRT_FILENAME=ca.crt -e LDAP_TLS_DH_PARAM_FILENAME=dhparam.pem \
  -v "$WORK/certs:/container/service/slapd/assets/certs:ro" \
  "$IMAGE" --copy-service >/dev/null

# ждём именно LDAPS: ldap:// отвечает уже временный slapd первичной настройки, до перезапуска с TLS
ready() { docker exec "$NAME" env LDAPTLS_REQCERT=never ldapwhoami -x -H ldaps://localhost:636 >/dev/null 2>&1; }
for _ in $(seq 1 90); do ready && break; sleep 1; done
ready || { docker logs "$NAME" 2>&1 | tail -30; exit 1; }

sed "s/@USER_PASSWORD@/$USER_PW/" "$HERE/seed.ldif" > "$WORK/seed.ldif"
docker cp "$WORK/seed.ldif" "$NAME:/tmp/seed.ldif" >/dev/null
# пароль администратора — через файл в контейнере, а не аргументом командной строки
printf %s "$ADMIN_PW" | docker exec -i "$NAME" sh -c \
  'umask 077; cat >/tmp/.pw; ldapadd -x -H ldap://localhost -D "cn=admin,dc=example,dc=org" -y /tmp/.pw -f /tmp/seed.ldif >/dev/null; rc=$?; rm -f /tmp/.pw /tmp/seed.ldif; exit $rc'

docker cp "$HERE/acl.ldif" "$NAME:/tmp/acl.ldif" >/dev/null
docker exec "$NAME" ldapmodify -Y EXTERNAL -H ldapi:/// -Q -f /tmp/acl.ldif >/dev/null

PORT="$(docker port "$NAME" 389/tcp | head -1 | sed 's/.*://')"
TLS_PORT="$(docker port "$NAME" 636/tcp | head -1 | sed 's/.*://')"

export MFA_GATE_TEST_LDAP_URL="ldap://127.0.0.1:$PORT"
export MFA_GATE_TEST_LDAPS_PORT="$TLS_PORT"
export MFA_GATE_TEST_LDAP_BASE_DN="$BASE_DN"
export MFA_GATE_TEST_LDAP_BIND_DN="cn=readonly,$BASE_DN"
export MFA_GATE_TEST_LDAP_BIND_PASSWORD="$RO_PW"
export MFA_GATE_TEST_LDAP_USER_PASSWORD="$USER_PW"
export MFA_GATE_TEST_LDAP_CA="$WORK/certs/ca.crt"
export MFA_GATE_TEST_LDAP_WRONG_CA="$WORK/certs/other-ca.crt"

cd "$SERVER"
set +e
"$SERVER/.venv/bin/pytest" -m ldap tests/test_ldap.py "$@"
rc=$?
exit $rc
