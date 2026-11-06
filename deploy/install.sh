#!/usr/bin/env bash
# Установка Tg-trending на VPS: Ubuntu / Debian (apt) или Alma / Rocky / CentOS / Fedora (dnf).
#
#   curl -fsSL https://raw.githubusercontent.com/gytfel/Tg-trending-/refs/heads/main/deploy/install.sh -o install.sh
#   sudo bash install.sh
#
# Скрипт можно запускать повторно: он обновит код и зависимости, а .env и базу не тронет.
#
# Необязательные переменные:
#   BRANCH=main               ветка репозитория
#   INSTALL_DIR=/opt/tg-trending
#   SERVICE=tg-trending       имя systemd-сервиса и команды управления
#   APP_USER=tgbot            системный пользователь, от которого работает бот
#   BOT_TOKEN=... ANTHROPIC_API_KEY=... ADMIN_IDS=...   чтобы не отвечать на вопросы
#   RECONFIGURE=1             заново спросить токен и ключи
#   USE_UV=1                  поставить отдельный Python через uv, даже если системный подходит
#   SKIP_PROBE=1              не проверять источники данных в конце
set -Eeuo pipefail

REPO_URL="${REPO_URL:-https://github.com/gytfel/Tg-trending-.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/tg-trending}"
SERVICE="${SERVICE:-tg-trending}"
APP_USER="${APP_USER:-tgbot}"
UV_PYTHON="3.12"   # ставится через uv, если системный Python старше 3.11

if [ -t 1 ]; then
  G=$'\e[32m' Y=$'\e[33m' R=$'\e[31m' B=$'\e[1m' N=$'\e[0m'
else
  G="" Y="" R="" B="" N=""
fi
step() { printf '\n%s==>%s %s%s%s\n' "$G" "$N" "$B" "$*" "$N"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '%s[!]%s %s\n' "$Y" "$N" "$*" >&2; }
die()  { printf '%s[x]%s %s\n' "$R" "$N" "$*" >&2; exit 1; }
trap 'die "Ошибка в строке $LINENO. Исправь причину и запусти скрипт ещё раз — он продолжит с того же места."' ERR

has_tty() { (: </dev/tty) 2>/dev/null; }

# ask VAR "вопрос" [secret] — читает ответ с терминала, даже если скрипт пришёл через pipe
ask() {
  local __var=$1 __prompt=$2 __secret=${3:-} __val=""
  if [ "$__secret" = secret ]; then
    read -r -s -p "$__prompt" __val </dev/tty || true
    echo >/dev/tty
  else
    read -r -p "$__prompt" __val </dev/tty || true
  fi
  __val="${__val#"${__val%%[![:space:]]*}"}"   # обрезаем пробелы по краям
  __val="${__val%"${__val##*[![:space:]]}"}"
  printf -v "$__var" '%s' "$__val"
}

[ "$(id -u)" -eq 0 ] || die "Нужны права root: sudo bash $0"
[ "${BASH_VERSINFO[0]}" -ge 4 ] || die "Нужен bash 4+"

# --- 1. Системные пакеты ------------------------------------------------------------
step "Ставлю системные пакеты"
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq git curl ca-certificates python3 python3-venv python3-pip >/dev/null
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y -q git curl ca-certificates python3 python3-pip >/dev/null
else
  die "Не нашёл apt-get или dnf. Поддерживаются Ubuntu/Debian и RHEL-семейство (Alma, Rocky, CentOS, Fedora)."
fi
info "git, curl, python3 — готово"

# --- 2. Код ---------------------------------------------------------------------------
step "Получаю код в $INSTALL_DIR"
if [ -d "$INSTALL_DIR/.git" ]; then
  BRANCH="${BRANCH:-$(git -C "$INSTALL_DIR" rev-parse --abbrev-ref HEAD)}"
  git -C "$INSTALL_DIR" fetch --quiet origin "$BRANCH"
  git -C "$INSTALL_DIR" checkout --quiet "$BRANCH"
  git -C "$INSTALL_DIR" pull --quiet --ff-only origin "$BRANCH" \
    || die "Не получилось обновить код: в $INSTALL_DIR есть локальные правки. Посмотри: git -C $INSTALL_DIR status"
  info "Обновил ветку $BRANCH до $(git -C "$INSTALL_DIR" rev-parse --short HEAD)"
else
  BRANCH="${BRANCH:-main}"
  [ ! -e "$INSTALL_DIR" ] || [ -z "$(ls -A "$INSTALL_DIR")" ] \
    || die "$INSTALL_DIR уже существует и это не git-репозиторий. Укажи другой каталог: sudo INSTALL_DIR=/opt/другой bash $0"
  git clone --quiet --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR" \
    || die "Не удалось скачать $REPO_URL (ветка $BRANCH). Проверь интернет и имя ветки."
  info "Скачал ветку $BRANCH ($(git -C "$INSTALL_DIR" rev-parse --short HEAD))"
fi
[ -f "$INSTALL_DIR/bot/__main__.py" ] \
  || die "В ветке $BRANCH нет кода бота. Укажи ветку с кодом: sudo BRANCH=имя-ветки bash $0"

# --- 3. Python 3.11+ ------------------------------------------------------------------
step "Проверяю Python"
PY=""
if [ "${USE_UV:-0}" != 1 ]; then
  for cand in python3.13 python3.12 python3.11 python3; do
    if command -v "$cand" >/dev/null 2>&1 \
       && "$cand" -c 'import sys, ensurepip, venv; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
      PY="$(command -v "$cand")"
      break
    fi
  done
fi
if [ -z "$PY" ]; then
  info "Ставлю отдельный Python $UV_PYTHON через uv (в /opt/uv-python) — системный не трогаю"
  export UV_INSTALL_DIR=/usr/local/bin UV_PYTHON_INSTALL_DIR=/opt/uv-python UV_NO_MODIFY_PATH=1
  if ! command -v uv >/dev/null 2>&1; then
    if ! curl -LsSf https://astral.sh/uv/install.sh 2>/dev/null | sh >/dev/null 2>&1; then
      info "astral.sh недоступен — беру uv из PyPI"
      python3 -m venv /opt/uv-tool
      /opt/uv-tool/bin/python -m pip install --quiet --disable-pip-version-check uv
      ln -sf /opt/uv-tool/bin/uv /usr/local/bin/uv
    fi
  fi
  uv python install --quiet "$UV_PYTHON"
  PY="$(uv python find --managed-python "$UV_PYTHON" 2>/dev/null || uv python find "$UV_PYTHON")"
fi
info "$("$PY" --version) — $PY"

# --- 4. Виртуальное окружение и зависимости -----------------------------------------------
step "Ставлю зависимости"
VENV="$INSTALL_DIR/.venv"
if [ -x "$VENV/bin/python" ] && ! "$VENV/bin/python" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  rm -rf "$VENV"   # старое окружение на неподходящем Python
fi
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r "$INSTALL_DIR/requirements.txt"
info "aiogram $("$VENV/bin/python" -c 'import aiogram; print(aiogram.__version__)') и остальные пакеты — готово"

# --- 5. Пользователь для сервиса -------------------------------------------------------------
if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$INSTALL_DIR" --no-create-home \
    --shell "$(command -v nologin || echo /usr/sbin/nologin)" "$APP_USER"
  info "Создал системного пользователя $APP_USER"
fi

# --- 6. Настройки (.env) -------------------------------------------------------------------
step "Настройки"
ENV_FILE="$INSTALL_DIR/.env"
FIRST_RUN=0
if [ ! -f "$ENV_FILE" ]; then
  cp "$INSTALL_DIR/.env.example" "$ENV_FILE"
  FIRST_RUN=1
fi
chown root:"$APP_USER" "$ENV_FILE"
chmod 640 "$ENV_FILE"

get_env() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1; }
set_env() {
  "$VENV/bin/python" - "$ENV_FILE" "$1" "$2" <<'PY'
import pathlib, re, sys
path, key, value = sys.argv[1:]
p = pathlib.Path(path)
lines = p.read_text(encoding="utf-8").splitlines()
for i, line in enumerate(lines):
    if re.match(rf"^{re.escape(key)}=", line):
        lines[i] = f"{key}={value}"
        break
else:
    lines.append(f"{key}={value}")
p.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

token_format_ok() {
  [[ "$1" =~ ^[0-9]{5,}:[A-Za-z0-9_-]{30,}$ ]] && [[ "$1" != *replace-with* ]]
}

# 0 — токен рабочий (имя бота в BOT_USERNAME), 1 — Telegram отверг токен, 2 — не достучались
BOT_USERNAME=""
check_token() {
  local resp
  # токен передаём через stdin, чтобы он не светился в списке процессов
  resp="$(printf 'url = "https://api.telegram.org/bot%s/getMe"\n' "$1" | curl -sS -m 15 -K - 2>/dev/null)" || return 2
  BOT_USERNAME="$(printf '%s' "$resp" | "$VENV/bin/python" -c \
    'import json, sys; d = json.load(sys.stdin); print(d["result"]["username"] if d.get("ok") else "")' 2>/dev/null)" || return 2
  [ -n "$BOT_USERNAME" ] && return 0
  return 1
}

TOKEN="${BOT_TOKEN:-$(get_env BOT_TOKEN)}"
if [ "${RECONFIGURE:-0}" = 1 ] && [ -z "${BOT_TOKEN:-}" ]; then
  TOKEN=""
fi
for attempt in 1 2 3; do
  if ! token_format_ok "$TOKEN"; then
    has_tty || die "BOT_TOKEN не задан или неверного вида. Запусти так: sudo BOT_TOKEN=123456:ABC... bash $0"
    [ "$attempt" = 1 ] && info "Токен бота выдаёт @BotFather в Telegram (команда /newbot)."
    ask TOKEN "    Вставь токен бота (ввод скрыт): " secret
    token_format_ok "$TOKEN" || { warn "Не похоже на токен: нужен вид 123456789:AAH...xyz"; TOKEN=""; continue; }
  fi
  rc=0
  check_token "$TOKEN" || rc=$?
  if [ "$rc" = 0 ]; then
    info "Токен рабочий: бот @$BOT_USERNAME"
    break
  elif [ "$rc" = 2 ]; then
    warn "Не достучался до api.telegram.org, токен проверить не удалось. Оставляю как есть."
    break
  fi
  warn "Telegram не принял этот токен."
  TOKEN=""
  has_tty || die "Неверный BOT_TOKEN."
done
token_format_ok "$TOKEN" || die "Токен так и не получен. Запусти скрипт ещё раз."
set_env BOT_TOKEN "$TOKEN"

# необязательные настройки: спрашиваем при первой установке или с RECONFIGURE=1
ASK_OPTIONAL=0
if has_tty && { [ "$FIRST_RUN" = 1 ] || [ "${RECONFIGURE:-0}" = 1 ]; }; then
  ASK_OPTIONAL=1
fi

if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  set_env ANTHROPIC_API_KEY "$ANTHROPIC_API_KEY"
elif [ "$ASK_OPTIONAL" = 1 ]; then
  info "Ключ Claude включает ИИ-вывод и навыки (console.anthropic.com → API Keys). Без него бот тоже работает."
  ask KEY "    Вставь ANTHROPIC_API_KEY или нажми Enter, чтобы пропустить (ввод скрыт): " secret
  if [ -n "$KEY" ]; then set_env ANTHROPIC_API_KEY "$KEY"; info "Ключ сохранён"; fi
fi

if [ -n "${ADMIN_IDS:-}" ]; then
  set_env ADMIN_IDS "$ADMIN_IDS"
elif [ "$ASK_OPTIONAL" = 1 ]; then
  info "Админ получает безлимитный запас и команду /stats. Свой ID узнаешь у @userinfobot."
  ask ADM "    Твой Telegram ID (Enter — пропустить): "
  if [[ "$ADM" =~ ^[0-9,\ ]+$ ]]; then set_env ADMIN_IDS "${ADM// /}"; info "ADMIN_IDS=${ADM// /}"; fi
fi

install -d -o "$APP_USER" -g "$APP_USER" -m 750 "$INSTALL_DIR/data"
info "Настройки: $ENV_FILE · база: $INSTALL_DIR/data"

# --- 7. Команда управления ---------------------------------------------------------------------
chmod +x "$INSTALL_DIR/deploy/manage.sh"
ln -sf "$INSTALL_DIR/deploy/manage.sh" "/usr/local/bin/$SERVICE"

# --- 8. systemd ------------------------------------------------------------------------------------
step "Запускаю сервис $SERVICE"
if [ -d /run/systemd/system ]; then
  cat >"/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=Tg-trending: Telegram-бот ИИ-аналитик
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV/bin/python -m bot
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# Изоляция: писать бот может только в каталог data/
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$INSTALL_DIR/data

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --quiet "$SERVICE"
  systemctl restart "$SERVICE"
  sleep 5
  if systemctl is-active --quiet "$SERVICE"; then
    info "Сервис работает и сам перезапустится после сбоя или перезагрузки сервера"
  else
    warn "Сервис не запустился. Последние строки лога:"
    journalctl -u "$SERVICE" -n 30 --no-pager || true
    die "Проверь настройки: $SERVICE config, затем $SERVICE restart"
  fi
else
  warn "systemd не найден (контейнер?). Автозапуск не настроен. Запустить вручную:"
  info "cd $INSTALL_DIR && sudo -u $APP_USER $VENV/bin/python -m bot"
fi

# --- 9. Проверка источников --------------------------------------------------------------------
if [ "${SKIP_PROBE:-0}" != 1 ]; then
  step "Проверяю, какие источники данных доступны с этого сервера (до минуты)"
  summary="$(cd "$INSTALL_DIR" && timeout 120 "$VENV/bin/python" -m bot.sources.probe BTC 2>/dev/null | tail -n 1)" || true
  info "${summary:-не удалось выполнить проверку}"
  info "Подробно по каждому источнику: $SERVICE probe BTC"
fi

# --- Готово -------------------------------------------------------------------------------------
step "Готово"
[ -n "$BOT_USERNAME" ] && info "Открой бота: https://t.me/$BOT_USERNAME и нажми /start"
cat <<EOF

    Управление ботом:
      $SERVICE status     — работает ли
      $SERVICE logs       — логи в реальном времени (выход: Ctrl+C)
      $SERVICE restart    — перезапуск
      $SERVICE config     — изменить настройки (.env) и перезапустить
      $SERVICE update     — обновить код с GitHub и перезапустить
      $SERVICE probe BTC  — проверить источники данных
      $SERVICE uninstall  — удалить сервис

EOF
