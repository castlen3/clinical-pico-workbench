#!/bin/zsh
set -u

DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="${DIR}/.pico_demo_server.pid"
PORT="8790"

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

PORT_PIDS="$(/usr/sbin/lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "$PORT_PIDS" ]]; then
  for pid in ${(f)PORT_PIDS}; do
    stop_pid "$pid"
  done
fi

rm -f "$PID_FILE"
echo "Lite Demo 服務已停止"
exit 0
