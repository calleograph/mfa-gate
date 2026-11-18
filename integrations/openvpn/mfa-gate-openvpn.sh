#!/usr/bin/env bash
# OpenVPN → MFA Gate без RADIUS: auth-user-pass-verify с отложенной проверкой (OpenVPN 2.5+: отложенный ответ скрипта через auth_control_file).
#
# server.conf:
#   script-security 2
#   auth-user-pass-verify /etc/openvpn/mfa-gate-openvpn.sh via-file
#   auth-gen-token 0 external-auth          # не спрашивать MFA при каждой смене ключей
#   reneg-sec 0
#   # ввод кода TOTP в клиенте (необязательно): static-challenge "Код MFA (пусто — подтверждение на телефоне)" 1
#
# /etc/openvpn/mfa-gate.conf — читается от имени пользователя OpenVPN (user nobody / openvpn после понижения прав):
#   chown root:nogroup /etc/openvpn/mfa-gate.conf && chmod 640 /etc/openvpn/mfa-gate.conf   (группа — та, что в «group» server.conf)
# Содержимое:
#   MFA_GATE_URL=http://192.0.2.10:8080
#   MFA_GATE_KEY=tf_xxxxxxxx:секрет          # ключ API-клиента с правом auth:write, привязанный к ресурсу VPN
#   MFA_GATE_WAIT=50                          # сколько ждать подтверждения на телефоне, секунд
#
# Ключ API и пароль не попадают в аргументы процессов (их не видно в ps): ключ передаётся curl через дескриптор файла,
# пароль — в теле запроса через stdin. Любая ошибка (сеть, ответ сервера, таймаут) — отказ во входе.
set -euo pipefail

CONF="${MFA_GATE_OPENVPN_CONF:-/etc/openvpn/mfa-gate.conf}"
# shellcheck disable=SC1090
. "$CONF"
: "${MFA_GATE_URL:?не задан MFA_GATE_URL в $CONF}" "${MFA_GATE_KEY:?не задан MFA_GATE_KEY в $CONF}"
WAIT="${MFA_GATE_WAIT:-50}"
CREDS_FILE="$1"
CONTROL_FILE="${auth_control_file:?OpenVPN не передал auth_control_file (нужен OpenVPN 2.5+ и via-file)}"

USERNAME="$(sed -n '1p' "$CREDS_FILE")"
PASSWORD="$(sed -n '2p' "$CREDS_FILE")"
OTP=""
if [[ "$PASSWORD" == SCRV1:* ]]; then   # static-challenge: SCRV1:<base64 пароль>:<base64 ответ>
  IFS=':' read -r _ p64 o64 <<< "$PASSWORD"
  PASSWORD="$(printf '%s' "$p64" | base64 -d)"
  OTP="$(printf '%s' "$o64" | base64 -d | tr -dc '0-9')"
fi

json_str() {   # строка JSON: экранирование \ и ", управляющие символы отбрасываются
  local s="${1//\\/\\\\}"
  s="${s//\"/\\\"}"
  printf '"%s"' "$(printf '%s' "$s" | tr -d '\000-\037')"
}

api() {   # api METHOD PATH [тело из stdin]
  curl -fsS --max-time "$((WAIT + 15))" -K <(printf 'user = "%s"\n' "$MFA_GATE_KEY") \
    -H 'Content-Type: application/json' -X "$1" "${MFA_GATE_URL%/}$2" --data-binary @-
}

field() { sed -n "s/.*\"$1\": *\"\\([^\"]*\\)\".*/\\1/p"; }

decide() { printf '%s\n' "$1" > "$CONTROL_FILE"; logger -t mfa-gate-openvpn "user=$USERNAME result=$2" 2>/dev/null || true; exit 0; }

(
  body="{\"login\":$(json_str "$USERNAME"),\"password\":$(json_str "$PASSWORD"),\"source_ip\":$(json_str "${untrusted_ip:-}"),\"client_name\":$(json_str "OpenVPN ${common_name:-}")"
  [[ -n "$OTP" ]] && body+=",\"otp\":$(json_str "$OTP")"
  body+="}"
  resp="$(printf '%s' "$body" | api POST /api/v1/access-requests)" || decide 0 "error"
  status="$(field status <<< "$resp")"
  id="$(field id <<< "$resp")"
  deadline=$((SECONDS + WAIT))
  while [[ "$status" == pending && $SECONDS -lt $deadline ]]; do
    resp="$(printf '' | api GET "/api/v1/access-requests/$id?wait=25")" || decide 0 "error"
    status="$(field status <<< "$resp")"
  done
  [[ "$status" == accepted ]] && decide 1 "accepted"
  decide 0 "${status:-error}"
) </dev/null >/dev/null 2>&1 &

exit 2   # решение придёт позже через auth_control_file
