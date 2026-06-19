#!/bin/zsh
set -u

DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="${DIR}/.demo_tunnel.pid"
LOG_FILE="${DIR}/.demo_tunnel.log"
TARGET_URL="http://127.0.0.1:8790"
HEALTH_URL="${TARGET_URL}/api/health"

is_running() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1
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

if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  stop_pid "$OLD_PID"
  rm -f "$PID_FILE"
fi

HEALTH_CODE="$(curl -s -o /dev/null -w "%{http_code}" --max-time 4 "$HEALTH_URL" || true)"
if [[ "$HEALTH_CODE" != "200" ]]; then
  echo "Demo 8790 尚未就緒（health=${HEALTH_CODE:-000}）。"
  echo "請先執行 start_lite_demo.command 並輸入 API key，再啟動 tunnel。"
  exit 1
fi

: > "$LOG_FILE"
nohup cloudflared tunnel --no-autoupdate --protocol http2 --ha-connections 2 --url "$TARGET_URL" > "$LOG_FILE" 2>&1 &
NEW_PID="$!"
echo "$NEW_PID" > "$PID_FILE"

URL=""
for _ in {1..40}; do
  URL="$(grep -Eo 'https://[-a-z0-9]+\.trycloudflare\.com' "$LOG_FILE" | head -n 1 || true)"
  if [[ -n "$URL" ]]; then
    break
  fi
  sleep 0.5
done

if [[ -n "$URL" ]]; then
  echo "Demo Tunnel 已啟動：$URL"
else
  echo "Demo Tunnel 啟動中，尚未取得 URL，請查看：$LOG_FILE"
fi

exit 0
