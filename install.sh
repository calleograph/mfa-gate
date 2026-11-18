#!/usr/bin/env bash
# MFA Gate — установка All-in-One на Ubuntu/Debian: все компоненты на одном сервере.
#
#   git clone https://github.com/calleograph/mfa-gate.git
#   cd mfa-gate
#   sudo ./install.sh
#
# Повторный запуск безопасен: существующие настройки, секреты и данные сохраняются.
# Переопределения (необязательно): MFA_GATE_HTTP_PORT, MFA_GATE_RADIUS_PORT, MFA_GATE_PUBLIC_URL, COMPOSE_PROJECT_NAME.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY="$ROOT/deploy"
ENV_FILE="$DEPLOY/.env"
CTL="$DEPLOY/mfagatectl"
PRODUCT_VERSION="$(cat "$ROOT/VERSION")"
LOG_DIR="/var/log/mfa-gate"
LOG="$LOG_DIR/install-$(date +%Y%m%d-%H%M%S).log"
STEPS=6
STEP=0
STAGE="начало"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
step() { STEP=$((STEP + 1)); STAGE="$1"; printf '\n\033[1m[%d/%d] %s\033[0m\n' "$STEP" "$STEPS" "$1"; echo "### [$STEP/$STEPS] $1" >> "$LOG"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; echo "OK $*" >> "$LOG"; }
info() { printf '  · %s\n' "$*"; echo "-- $*" >> "$LOG"; }
fail() {
  printf '\n\033[31mОШИБКА на этапе «%s»:\033[0m %s\n' "$STAGE" "$1" >&2
  [[ -n "${2:-}" ]] && printf '\033[1mКак исправить:\033[0m %s\n' "$2" >&2
  printf 'Журнал установки: %s — приложите его, если обращаетесь за помощью.\n' "$LOG" >&2
  echo "FAIL stage=\"$STAGE\" reason=\"$1\"" >> "$LOG"
  exit 1
}
run() { "$@" >> "$LOG" 2>&1; }   # команда с выводом только в журнал
trap 'fail "команда завершилась ошибкой (строка $LINENO)" "посмотрите последние строки журнала"' ERR

# ------------------------------------------------------------------------------------------------ права и журнал
if [[ $EUID -ne 0 ]]; then
  echo "Запустите установку с правами администратора: sudo ./install.sh" >&2
  exit 1
fi
mkdir -p "$LOG_DIR" && chmod 700 "$LOG_DIR" && : > "$LOG" && chmod 600 "$LOG"
# root будет запускать скрипты и читать конфигурацию из этого каталога: менять их должен только root
if [[ "$(stat -c %u "$ROOT")" != 0 ]] || find "$ROOT" -path "$ROOT/.git" -prune -o -perm /022 -print -quit 2>/dev/null | grep -q .; then
  chown -R root:root "$ROOT" && chmod -R go-w "$ROOT"
  echo "каталог $ROOT передан root (иначе обычный пользователь мог бы подменить скрипты, которые выполняются от root)."
  echo "обновление исходников теперь: sudo git -C $ROOT pull"
fi
bold "MFA Gate $PRODUCT_VERSION — установка All-in-One"
echo "журнал: $LOG"

# ------------------------------------------------------------------------------------------------ 1. система
step "Проверка сервера"
# os-release читается в подоболочке: он определяет свои VERSION/ID/NAME, которые иначе затёрли бы переменные установщика
os_id="$(. /etc/os-release 2>/dev/null && echo "${ID:-}")"
os_name="$(. /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-$ID}")"
case "$os_id" in
  ubuntu|debian) ok "ОС: $os_name" ;;
  *) fail "ОС ${os_name:-неизвестна} не поддерживается установщиком" "используйте Ubuntu 22.04 и новее или Debian 12; на других ОС — ручная установка (docs/install.md)" ;;
esac
case "$(uname -m)" in x86_64|aarch64) ok "архитектура $(uname -m)" ;; *) fail "архитектура $(uname -m) не поддерживается" "нужен x86_64 или aarch64" ;; esac
mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
(( mem_mb >= 900 )) && ok "память: ${mem_mb} МБ" || fail "мало памяти: ${mem_mb} МБ" "нужно не меньше 1 ГБ"
free_gb=$(df -BG --output=avail "$ROOT" | tail -n1 | tr -dc 0-9)
(( free_gb >= 5 )) && ok "свободно на диске: ${free_gb} ГБ" || fail "мало места на диске: ${free_gb} ГБ" "освободите место (нужно не меньше 5 ГБ) или расширьте раздел"
if curl -fsS --max-time 10 -o /dev/null https://ghcr.io/ || curl -fsS --max-time 10 -o /dev/null https://github.com/; then
  ok "доступ в Интернет есть"
else
  fail "нет доступа к ghcr.io и github.com" "проверьте DNS, шлюз и прокси (переменные https_proxy), затем повторите"
fi

# ------------------------------------------------------------------------------------------------ 2. пакеты и Docker
step "Docker и необходимые пакеты"
need=()
for cmd in curl openssl ss realpath; do command -v "$cmd" >/dev/null || need+=("$cmd"); done
if (( ${#need[@]} )); then
  info "установка: ${need[*]}"
  run apt-get update -q
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y -q curl openssl iproute2 coreutils
fi
if ! command -v docker >/dev/null; then
  info "Docker не найден — установка из репозитория дистрибутива"
  run apt-get update -q
  if ! run env DEBIAN_FRONTEND=noninteractive apt-get install -y -q docker.io docker-compose-v2; then
    info "пакеты дистрибутива недоступны — установка официальным скриптом get.docker.com"
    getdocker="$(mktemp)"
    curl -fsSL https://get.docker.com -o "$getdocker" >> "$LOG" 2>&1 || fail "не удалось скачать установщик Docker" "проверьте доступ к get.docker.com"
    run sh "$getdocker" || fail "установка Docker не удалась" "см. журнал; можно установить Docker вручную: https://docs.docker.com/engine/install/"
    rm -f "$getdocker"
  fi
fi
run systemctl enable --now docker || true
docker info >/dev/null 2>&1 || fail "служба Docker не запущена" "systemctl status docker; journalctl -u docker"
ok "Docker $(docker version --format '{{.Server.Version}}')"
if ! docker compose version >/dev/null 2>&1; then
  run apt-get install -y -q docker-compose-v2 || run apt-get install -y -q docker-compose-plugin \
    || fail "нет docker compose v2" "установите пакет docker-compose-v2 или docker-compose-plugin"
fi
ok "$(docker compose version)"

# ------------------------------------------------------------------------------------------------ 3. конфигурация
step "Конфигурация"
port_busy() { ss -H "$2" "sport = :$1" 2>/dev/null | grep -q .; }
running_here=0
[[ -f "$ENV_FILE" ]] && docker ps --format '{{.Label "com.docker.compose.project"}}' | grep -qx "$(grep -m1 '^COMPOSE_PROJECT_NAME=' "$ENV_FILE" | cut -d= -f2)" && running_here=1
if [[ -f "$ENV_FILE" ]]; then
  ok "найдена существующая конфигурация $ENV_FILE — секреты и настройки сохраняются"
else
  ip_addr="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i=1;i<=NF;i++) if ($i=="src") print $(i+1)}' | head -n1)"
  [[ -n "$ip_addr" ]] || ip_addr="$(hostname -I | awk '{print $1}')"
  http_port="${MFA_GATE_HTTP_PORT:-8080}"
  if [[ -z "${MFA_GATE_HTTP_PORT:-}" ]]; then
    while port_busy "$http_port" -ltn && (( http_port < 8100 )); do http_port=$((http_port + 1)); done
  fi
  port_busy "$http_port" -ltn && fail "порт $http_port/tcp занят" "задайте свободный порт: sudo MFA_GATE_HTTP_PORT=8180 ./install.sh"
  radius_port="${MFA_GATE_RADIUS_PORT:-1812}"
  port_busy "$radius_port" -lun && fail "порт $radius_port/udp (RADIUS) занят другой программой" \
    "освободите порт или задайте другой: sudo MFA_GATE_RADIUS_PORT=11812 ./install.sh (этот порт укажите на VPN-шлюзе)"
  cp "$DEPLOY/.env.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  set_env() { if grep -q "^$1=" "$ENV_FILE"; then sed -i "s|^$1=.*|$1=$2|" "$ENV_FILE"; else echo "$1=$2" >> "$ENV_FILE"; fi; }
  set_env COMPOSE_PROJECT_NAME "${COMPOSE_PROJECT_NAME:-mfa-gate}"
  set_env MFA_GATE_VERSION "$PRODUCT_VERSION"
  set_env MFA_GATE_HTTP_PORT "$http_port"
  set_env MFA_GATE_RADIUS_PORT "$radius_port"
  set_env MFA_GATE_PUBLIC_URL "${MFA_GATE_PUBLIC_URL:-http://$ip_addr:$http_port}"
  set_env POSTGRES_PASSWORD "$(openssl rand -hex 24)"
  ok "создан $ENV_FILE (права 600)"
fi
# shellcheck disable=SC1090
bash -n "$ENV_FILE" 2>>"$LOG" || fail "файл $ENV_FILE повреждён (синтаксическая ошибка)" "исправьте строку, указанную в журнале, или удалите файл и запустите установку заново"
set -a; . "$ENV_FILE"; set +a
info "адрес панели: $MFA_GATE_PUBLIC_URL"
info "RADIUS: UDP $MFA_GATE_RADIUS_BIND:$MFA_GATE_RADIUS_PORT"
(( running_here )) && info "экземпляр уже запущен — будет обновлён до состояния конфигурации"

# ------------------------------------------------------------------------------------------------ 4–6. образы, запуск, миграции
step "Образы, база данных и запуск"
info "это займёт 1–5 минут; подробности — в журнале"
trap - ERR
if ! "$CTL" install >> "$LOG" 2>&1; then
  reason="$(grep -E 'FAIL|ERROR|Error|error' "$LOG" | tail -n 3 | tr '\n' ' ')"
  fail "контейнеры не запустились: ${reason:-см. журнал}" "проверьте: sudo $CTL status; sudo $CTL logs"
fi
trap 'fail "команда завершилась ошибкой (строка $LINENO)" "посмотрите последние строки журнала"' ERR
ok "PostgreSQL, миграции, api, radius и worker запущены и прошли проверку готовности"

# ------------------------------------------------------------------------------------------------ 7. администратор
step "Первый администратор"
# вывод только на экран: пароль не попадает ни в журнал установки, ни в журналы контейнеров
admin_out="$("$CTL" admin-init 2>&1)" || fail "не удалось создать администратора: $admin_out" "sudo $CTL logs api"
if grep -q "пропущено" <<< "$admin_out"; then
  ok "главный администратор уже существует (пароль не менялся)"
  ADMIN_PASSWORD=""
else
  ADMIN_PASSWORD="$(sed -n 's/^пароль (показывается один раз): //p' <<< "$admin_out")"
  [[ -n "$ADMIN_PASSWORD" ]] || fail "администратор создан, но пароль не получен" "создайте заново: sudo $CTL admin admin"
  ok "создан администратор admin с временным паролем"
  echo "OK администратор создан (пароль на экране, в журнал не записан)" >> "$LOG"
fi

# ------------------------------------------------------------------------------------------------ 8. проверка
step "Проверка работы"
ready="$(curl -fsS --max-time 10 "http://127.0.0.1:$MFA_GATE_HTTP_PORT/health/ready" 2>>"$LOG" || true)"
[[ "$ready" == *'"ok"'* ]] && ok "API отвечает: $ready" || fail "API не отвечает на /health/ready" "sudo $CTL logs api"
curl -fsS --max-time 10 "http://127.0.0.1:$MFA_GATE_HTTP_PORT/" 2>>"$LOG" | grep -q "MFA Gate" && ok "панель администратора открывается" \
  || fail "панель не открывается" "sudo $CTL logs api"
ss -H -lun "sport = :$MFA_GATE_RADIUS_PORT" | grep -q . && ok "RADIUS слушает UDP $MFA_GATE_RADIUS_PORT" || fail "RADIUS не слушает порт $MFA_GATE_RADIUS_PORT" "sudo $CTL logs radius"
ln -sf "$CTL" /usr/local/bin/mfagatectl && ok "команда управления: mfagatectl (status, logs, update, backup, diagnose …)"

printf '\n'
bold "Готово. MFA Gate $MFA_GATE_VERSION установлен."
cat <<EOF

  Панель администратора:  $MFA_GATE_PUBLIC_URL
EOF
if [[ -n "$ADMIN_PASSWORD" ]]; then
  cat <<EOF
  Логин:                  admin
  Временный пароль:       $ADMIN_PASSWORD
                          (показан один раз; при первом входе его нужно сменить, затем подключить второй фактор)
EOF
fi
cat <<EOF
  RADIUS для VPN-шлюзов:  UDP-порт $MFA_GATE_RADIUS_PORT на адресе этого сервера
  Данные и мастер-ключ:   $DEPLOY/data   (сохраните копию data/secrets/master.key отдельно!)

  Работа идёт по HTTP — только для изолированной сети и первичной настройки.
  HTTPS через reverse proxy: docs/install.md, раздел «HTTPS».

  Дальше: войдите в панель → подключите каталог LDAP/AD → ресурс → RADIUS-клиент → привяжите телефон.
  Управление: mfagatectl status | logs | update <версия> | backup | diagnose
EOF
