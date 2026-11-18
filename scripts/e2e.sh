#!/usr/bin/env bash
# Изолированный стенд в Docker: установка через mfagatectl → сквозные тесты → жизненный цикл → уборка.
# Порты случайные на 127.0.0.1, имя compose-проекта уникальное: рядом работающие проекты не затрагиваются.
# Использование: scripts/e2e.sh [--keep]   (--keep — не удалять стенд после прогона)
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KEEP=0; [[ "${1:-}" == "--keep" ]] && KEEP=1
WORK="${MFA_GATE_E2E_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/mfa-gate-e2e.XXXXXX")}"
NAME="mfa-gate-e2e-$(openssl rand -hex 3)"
RESULTS="$WORK/results.txt"
CTL="$ROOT/deploy/mfagatectl"
export MFA_GATE_ENV_FILE="$WORK/.env"

pass=0; fail=0
ok()   { echo "PASS $*" | tee -a "$RESULTS"; pass=$((pass + 1)); }
bad()  { echo "FAIL $*" | tee -a "$RESULTS"; fail=$((fail + 1)); [[ -s "$WORK/last.log" ]] && tail -n 25 "$WORK/last.log" | sed 's/^/    | /'; }
run()  { "$@" > "$WORK/last.log" 2>&1; }   # вывод операции сохраняется и показывается при провале
step() { printf '\n\033[1m### %s\033[0m\n' "$*"; }

free_port() { python3 -c "import socket; s=socket.socket(socket.AF_INET, socket.SOCK_$1); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1])"; }

cleanup() {
  if (( KEEP )); then echo "стенд оставлен: MFA_GATE_ENV_FILE=$MFA_GATE_ENV_FILE $CTL status"; return; fi
  docker compose --project-directory "$ROOT/deploy" --env-file "$MFA_GATE_ENV_FILE" -f "$ROOT/deploy/compose.yml" -p "$NAME" down -v --remove-orphans >/dev/null 2>&1 || true
  # данные postgres принадлежат uid контейнера — удаляем изнутри
  docker run --rm -v "$WORK:/w" alpine:3 rm -rf /w/data /w/backups >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

dc() { docker compose --project-directory "$ROOT/deploy" --env-file "$MFA_GATE_ENV_FILE" -f "$ROOT/deploy/compose.yml" -p "$NAME" "$@"; }

step "стенд $NAME в $WORK"
HTTP_PORT="$(free_port STREAM)"; RADIUS_PORT="$(free_port DGRAM)"
cp "$ROOT/deploy/.env.example" "$MFA_GATE_ENV_FILE"; chmod 600 "$MFA_GATE_ENV_FILE"
sed -i -e "s|^COMPOSE_PROJECT_NAME=.*|COMPOSE_PROJECT_NAME=$NAME|" \
  -e "s|^MFA_GATE_IMAGE=.*|MFA_GATE_IMAGE=mfa-gate|" -e "s|^MFA_GATE_VERSION=.*|MFA_GATE_VERSION=0.1.0|" \
  -e "s|^MFA_GATE_DATA=.*|MFA_GATE_DATA=$WORK/data|" -e "s|^MFA_GATE_BACKUPS=.*|MFA_GATE_BACKUPS=$WORK/backups|" \
  -e "s|^MFA_GATE_PUBLIC_URL=.*|MFA_GATE_PUBLIC_URL=http://127.0.0.1:$HTTP_PORT|" \
  -e "s|^MFA_GATE_HTTP_PORT=.*|MFA_GATE_HTTP_PORT=$HTTP_PORT|" \
  -e "s|^MFA_GATE_RADIUS_BIND=.*|MFA_GATE_RADIUS_BIND=127.0.0.1|" -e "s|^MFA_GATE_RADIUS_PORT=.*|MFA_GATE_RADIUS_PORT=$RADIUS_PORT|" \
  -e "s|^MFA_GATE_PUSH_TIMEOUT=.*|MFA_GATE_PUSH_TIMEOUT=10|" -e "s|^MFA_GATE_COOKIE_SECURE=.*|MFA_GATE_COOKIE_SECURE=false|" \
  "$MFA_GATE_ENV_FILE"

step "установка"
if "$CTL" install; then ok "чистая установка через mfagatectl install"; else bad "установка"; exit 1; fi
if "$CTL" install >/dev/null; then ok "повторный install идемпотентен"; else bad "повторный install"; fi

KEY="$(dc exec -T api python -m mfa_gate create-api-client e2e --scopes all | tail -n1)"
ADMIN_PW="$(openssl rand -hex 12)"
echo "$ADMIN_PW" | dc exec -T api python -m mfa_gate create-admin e2e-root --temporary --password-stdin >/dev/null
if "$CTL" admin-init 2>&1 | grep -q "пропущено"; then ok "admin-init не трогает существующего администратора"; else bad "admin-init при наличии администратора"; fi
SUBNET="$(docker network inspect "${NAME}_backend" -f '{{(index .IPAM.Config 0).Gateway}}')/32"

step "сквозные тесты (RADIUS → push → подтверждение)"
if MFA_GATE_TEST_URL="http://127.0.0.1:$HTTP_PORT" MFA_GATE_TEST_RADIUS="127.0.0.1:$RADIUS_PORT" MFA_GATE_TEST_KEY="$KEY" \
   MFA_GATE_TEST_ADMIN="e2e-root:$ADMIN_PW" MFA_GATE_TEST_CLIENT_CIDR="$SUBNET" MFA_GATE_TEST_PUSH_TIMEOUT=10 \
   "$ROOT/server/.venv/bin/pytest" -q -p no:cacheprovider "$ROOT/server/tests/test_stack.py" 2>&1 | tee "$WORK/pytest.txt" | tail -n 3; then
  :
fi
summary="$(grep -E '[0-9]+ (passed|failed)' "$WORK/pytest.txt" | tail -n1)"
[[ "$summary" == *passed* && "$summary" != *failed* ]] && ok "сквозные тесты: $summary" || bad "сквозные тесты: $summary"

step "сохранность данных при пересоздании контейнеров"
users_before="$(dc exec -T postgres psql -U mfagate -d mfagate -tAc 'SELECT count(*) FROM users')"
dc down >/dev/null 2>&1 && "$CTL" start >/dev/null
users_after="$(dc exec -T postgres psql -U mfagate -d mfagate -tAc 'SELECT count(*) FROM users')"
[[ "$users_before" == "$users_after" && "$users_before" -gt 0 ]] && ok "пересоздание: пользователей $users_after, данные на месте" || bad "пересоздание: было $users_before, стало $users_after"

step "резервная копия и восстановление"
backup="$("$CTL" backup | tail -n1)"
[[ -f "$backup/db.dump" ]] && ok "резервная копия $backup" || bad "резервная копия"
dc exec -T postgres psql -U mfagate -d mfagate -qc "INSERT INTO groups (id, name, description) VALUES (gen_random_uuid(), 'после-копии', '')"
if run "$CTL" restore "$backup"; then
  left="$(dc exec -T postgres psql -U mfagate -d mfagate -tAc "SELECT count(*) FROM groups WHERE name='после-копии'")"
  [[ "$left" == 0 ]] && ok "восстановление вернуло состояние копии" || bad "восстановление: запись после копии осталась"
else bad "restore"; fi

step "обновление и откат"
docker tag mfa-gate:0.1.0 mfa-gate:0.1.1
if run "$CTL" update 0.1.1; then
  grep -q '^MFA_GATE_VERSION=0.1.1' "$MFA_GATE_ENV_FILE" && ok "обновление 0.1.0 → 0.1.1 (копия, миграции, healthy)" || bad "версия после update"
else bad "update"; fi
if run "$CTL" rollback; then
  grep -q '^MFA_GATE_VERSION=0.1.0' "$MFA_GATE_ENV_FILE" && ok "откат на 0.1.0 с восстановлением базы" || bad "версия после rollback"
else bad "rollback"; fi
docker rmi mfa-gate:0.1.1 >/dev/null 2>&1 || true
sed -i "s|^MFA_GATE_IMAGE=.*|MFA_GATE_IMAGE=127.0.0.1:9/absent/mfa-gate|" "$MFA_GATE_ENV_FILE"   # реестр недоступен
"$CTL" update 9.9.9 >/dev/null 2>&1 && bad "обновление на недоступный образ должно отменяться" \
  || { grep -q '^MFA_GATE_VERSION=0.1.0' "$MFA_GATE_ENV_FILE" && ok "недоступный образ → обновление отменено, версия 0.1.0" || bad "после сбоя обновления версия не возвращена"; }
sed -i "s|^MFA_GATE_IMAGE=.*|MFA_GATE_IMAGE=mfa-gate|" "$MFA_GATE_ENV_FILE"
code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$HTTP_PORT/health/ready")"
[[ "$code" == 200 ]] && ok "после отменённого обновления сервис работает" || bad "после отменённого обновления /health/ready = $code"

step "недоступность базы и восстановление соединения"
dc stop postgres >/dev/null
code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$HTTP_PORT/health/ready")"
[[ "$code" == 503 ]] && ok "без БД /health/ready = 503" || bad "без БД /health/ready = $code"
dc start postgres >/dev/null; sleep 6
code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$HTTP_PORT/health/ready")"
[[ "$code" == 200 ]] && ok "после возврата БД /health/ready = 200 без перезапуска api" || bad "после возврата БД /health/ready = $code"

step "диагностика"
if "$CTL" diagnose > "$WORK/diag.txt" 2>&1; then
  ok "diagnose: $(grep -c '^OK' "$WORK/diag.txt") проверок OK, $(grep -c '^WARN' "$WORK/diag.txt") предупреждений, $(grep -c '^FAIL' "$WORK/diag.txt") ошибок"
else bad "diagnose"; fi

printf '\nИтог: %d пройдено, %d провалено\n' "$pass" "$fail" | tee -a "$RESULTS"
exit $(( fail > 0 ))
