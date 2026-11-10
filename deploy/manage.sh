#!/usr/bin/env bash
# Управление ботом на сервере. install.sh ставит эту команду как /usr/local/bin/tg-trending.
set -Eeuo pipefail

SELF="$(readlink -f "${BASH_SOURCE[0]}")"
INSTALL_DIR="$(cd "$(dirname "$SELF")/.." && pwd)"
VENV="$INSTALL_DIR/.venv"
SERVICE="$(basename "$0")"
[ "$SERVICE" = "manage.sh" ] && SERVICE="tg-trending"
APP_USER="$(stat -c %U "$INSTALL_DIR/data" 2>/dev/null || echo tgbot)"

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    exec sudo -- "$SELF" "$@"
  fi
}

usage() {
  cat <<EOF
Использование: $SERVICE <команда>

  status          работает ли бот
  logs [N]        логи в реальном времени (последние N строк, по умолчанию 100)
  start | stop | restart
  config          открыть настройки (.env) в редакторе и перезапустить бота
  update          скачать новую версию с GitHub, обновить зависимости, перезапустить
  probe [ARGS]    проверить источники: probe BTC · probe --global · probe ETH -v
  uninstall       остановить и удалить сервис (код и база останутся)

Каталог: $INSTALL_DIR
EOF
}

cmd="${1:-help}"
[ $# -gt 0 ] && shift

case "$cmd" in
  status)
    systemctl status "$SERVICE" --no-pager
    ;;
  logs)
    journalctl -u "$SERVICE" -f -n "${1:-100}"
    ;;
  start | stop | restart)
    need_root "$cmd" "$@"
    systemctl "$cmd" "$SERVICE"
    systemctl is-active "$SERVICE" || true
    ;;
  config)
    need_root config "$@"
    editor="${EDITOR:-$(command -v nano || command -v vi)}"
    "$editor" "$INSTALL_DIR/.env"
    systemctl restart "$SERVICE"
    echo "Перезапустил $SERVICE с новыми настройками"
    ;;
  update)
    need_root update "$@"
    before="$(git -C "$INSTALL_DIR" rev-parse --short HEAD)"
    git -C "$INSTALL_DIR" pull --ff-only
    after="$(git -C "$INSTALL_DIR" rev-parse --short HEAD)"
    "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r "$INSTALL_DIR/requirements.txt"
    systemctl restart "$SERVICE"
    if [ "$before" = "$after" ]; then
      echo "Уже последняя версия ($after), бот перезапущен"
    else
      echo "Обновил $before → $after, бот перезапущен"
      git -C "$INSTALL_DIR" log --oneline "$before..$after" | head -n 20
    fi
    ;;
  probe)
    cd "$INSTALL_DIR"
    if [ $# -eq 0 ]; then set -- BTC; fi
    "$VENV/bin/python" -m bot.sources.probe "$@"
    ;;
  uninstall)
    need_root uninstall "$@"
    read -r -p "Остановить и удалить сервис $SERVICE? [y/N] " answer
    [[ "$answer" =~ ^[YyДд]$ ]] || { echo "Отменено"; exit 0; }
    systemctl disable --now "$SERVICE" 2>/dev/null || true
    rm -f "/etc/systemd/system/$SERVICE.service"
    systemctl daemon-reload
    rm -f "/usr/local/bin/$SERVICE"
    echo "Сервис удалён. Код, настройки и база остались в $INSTALL_DIR."
    echo "Удалить совсем: sudo rm -rf $INSTALL_DIR && sudo userdel $APP_USER"
    ;;
  help | -h | --help)
    usage
    ;;
  *)
    echo "Неизвестная команда: $cmd" >&2
    usage >&2
    exit 1
    ;;
esac
