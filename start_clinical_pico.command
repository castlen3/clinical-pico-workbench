#!/bin/zsh
set -u

DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="9999"
HOST="0.0.0.0"
URL="http://localhost:${PORT}"
HEALTH_URL="http://localhost:${PORT}/api/health"
PID_FILE="${DIR}/.pico_server.pid"
LOG_FILE="${DIR}/.pico_server.log"
LOCK_DIR="${DIR}/.pico_start.lock"
WINDOW_TITLE="Clinical PICO 啟動器"

printf '\033]0;%s\007' "$WINDOW_TITLE"

is_running() {
  local pid="$1"
  if [[ -z "$pid" ]]; then
    return 1
  fi
  kill -0 "$pid" >/dev/null 2>&1
}

stop_pid() {
  local pid="$1"
  if is_running "$pid"; then
    kill "$pid" >/dev/null 2>&1 || true
    sleep 0.4
    if is_running "$pid"; then
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
  fi
}

http_code() {
  /usr/bin/curl -s -o /dev/null -w "%{http_code}" --max-time 3 "$HEALTH_URL" || true
}

open_app() {
  nohup /usr/bin/open "$URL" >/dev/null 2>&1 &
}

cleanup_lock() {
  rmdir "$LOCK_DIR" >/dev/null 2>&1 || true
}

close_launcher_windows() {
  if [[ "${PICO_KEEP_WINDOW:-0}" == "1" ]]; then
    return
  fi
  (sleep 0.7; osascript <<'APPLESCRIPT'
tell application "Terminal"
  repeat with w in (every window)
    set windowName to name of w
    if windowName contains "Clinical PICO 啟動器" or windowName contains "start_clinical_pico.command" then
      close w saving no
    end if
  end repeat
end tell
APPLESCRIPT
) >/dev/null 2>&1 &
}

cleanup_on_exit() {
  cleanup_lock
  close_launcher_windows
}

if ! mkdir "$LOCK_DIR" >/dev/null 2>&1; then
  echo "Clinical PICO 正在啟動中，請稍候再試。"
  close_launcher_windows
  exit 0
fi
trap cleanup_on_exit EXIT

# 若服務已健康運作，直接沿用；不要先清 port，避免連點或修復腳本把服務砍掉。
if [[ "$(http_code)" == "200" ]]; then
  PORT_PID="$(/usr/sbin/lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | /usr/bin/head -n 1 || true)"
  if [[ -n "$PORT_PID" ]]; then
    echo "$PORT_PID" >"$PID_FILE"
  fi
  open_app
  echo "Clinical PICO 已在運行：http://localhost:${PORT}"
  exit 0
fi

if [[ -f "$PID_FILE" ]]; then
  EXISTING_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  stop_pid "$EXISTING_PID"
  rm -f "$PID_FILE"
fi

# 只有在健康檢查失敗時才清理佔用 9999 的程序。
PORT_PIDS="$(/usr/sbin/lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "$PORT_PIDS" ]]; then
  for pid in ${(f)PORT_PIDS}; do
    stop_pid "$pid"
  done
fi

cd "$DIR" || exit 1
: >"$LOG_FILE"
nohup /usr/bin/python3 "$DIR/server.py" --host "$HOST" --port "$PORT" >"$LOG_FILE" 2>&1 &
NEW_PID="$!"
echo "$NEW_PID" >"$PID_FILE"

HEALTH_OK="0"
for _ in {1..20}; do
  code="$(http_code)"
  if [[ "$code" == "200" ]]; then
    HEALTH_OK="1"
    break
  fi
  sleep 0.25
done

if [[ "$HEALTH_OK" == "1" ]] && is_running "$NEW_PID"; then
  PORT_PID="$(/usr/sbin/lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | /usr/bin/head -n 1 || true)"
  if [[ -n "$PORT_PID" ]]; then
    echo "$PORT_PID" >"$PID_FILE"
  fi
  open_app
  echo "Clinical PICO 已啟動：http://localhost:${PORT}"
else
  stop_pid "$NEW_PID"
  rm -f "$PID_FILE"
  echo "Clinical PICO 啟動失敗（API 健康檢查未通過），請查看 ${LOG_FILE}"
fi

exit 0
