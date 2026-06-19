#!/bin/zsh
set -u

DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="${DIR}/.demo_tunnel.pid"

stop_pid() {
  local pid="$1"
  if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
    kill "$pid" >/dev/null 2>&1 || true
    sleep 0.4
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
  fi
}

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  stop_pid "$PID"
fi

rm -f "$PID_FILE"
echo "Demo Tunnel 已停止"
exit 0
