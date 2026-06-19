#!/bin/zsh
set -u

DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="${DIR}/.pico_server.pid"
PORT="9999"
WINDOW_TITLE="Clinical PICO 停止器"

printf '\033]0;%s\007' "$WINDOW_TITLE"

close_launcher_windows() {
  if [[ "${PICO_KEEP_WINDOW:-0}" == "1" ]]; then
    return
  fi
  (sleep 0.7; osascript <<'APPLESCRIPT'
tell application "Terminal"
  repeat with w in (every window)
    set windowName to name of w
    if windowName contains "Clinical PICO 停止器" or windowName contains "stop_clinical_pico.command" then
      close w saving no
    end if
  end repeat
end tell
APPLESCRIPT
) >/dev/null 2>&1 &
}

trap close_launcher_windows EXIT

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

# 額外清理佔用 9999 的所有程序
PORT_PIDS="$(/usr/sbin/lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "$PORT_PIDS" ]]; then
  for pid in ${(f)PORT_PIDS}; do
    stop_pid "$pid"
  done
fi

rm -f "$PID_FILE"
echo "Clinical PICO 服務已停止"
exit 0
