#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/artifacts/runtime"
PID_FILE="$RUNTIME_DIR/caffeinate.pid"
LOG_FILE="$RUNTIME_DIR/caffeinate.log"

mkdir -p "$RUNTIME_DIR"

is_alive() {
  local pid="$1"
  if [[ -z "$pid" ]]; then
    return 1
  fi
  kill -0 "$pid" >/dev/null 2>&1
}

status() {
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if is_alive "$pid"; then
      echo "awake_guard=running pid=$pid"
      return 0
    fi
  fi
  echo "awake_guard=stopped"
  return 1
}

start() {
  if [[ -f "$PID_FILE" ]]; then
    local old_pid
    old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if is_alive "$old_pid"; then
      echo "already_running pid=$old_pid"
      return 0
    fi
  fi

  nohup caffeinate -dimsu >>"$LOG_FILE" 2>&1 &
  local pid="$!"
  echo "$pid" >"$PID_FILE"
  sleep 0.2
  if is_alive "$pid"; then
    echo "started pid=$pid"
    return 0
  fi
  echo "failed_to_start"
  return 1
}

stop() {
  if [[ ! -f "$PID_FILE" ]]; then
    echo "already_stopped"
    return 0
  fi

  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if is_alive "$pid"; then
    kill "$pid" >/dev/null 2>&1 || true
    sleep 0.2
  fi
  rm -f "$PID_FILE"
  echo "stopped"
}

case "${1:-start}" in
  start)
    start
    ;;
  stop)
    stop
    ;;
  status)
    status || exit 1
    ;;
  restart)
    stop
    start
    ;;
  *)
    echo "usage: $0 [start|stop|status|restart]"
    exit 2
    ;;
esac
