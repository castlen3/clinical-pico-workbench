#!/bin/zsh
set -u

DIR="$(cd "$(dirname "$0")" && pwd)"

MAIN_HEALTH="http://127.0.0.1:9999/api/health"
DEMO_HEALTH="http://127.0.0.1:8790/api/health"

http_code() {
  local url="$1"
  curl -s -o /dev/null -w "%{http_code}" --max-time 4 "$url" || true
}

echo "[1/4] 檢查 Main 9999"
if [[ "$(http_code "$MAIN_HEALTH")" != "200" ]]; then
  echo "Main 未上線，嘗試啟動..."
  PICO_KEEP_WINDOW=1 "${DIR}/start_clinical_pico.command" || true
  sleep 1
else
  echo "Main 正常"
fi

echo "[2/4] 檢查 Demo 8790"
if [[ "$(http_code "$DEMO_HEALTH")" != "200" ]]; then
  echo "Demo 未上線，嘗試啟動（可能會要求輸入 API key）..."
  "${DIR}/start_lite_demo.command" || true
  sleep 1
else
  echo "Demo 正常"
fi

echo "[3/4] 檢查 Tunnel"
"${DIR}/stop_demo_tunnel.command" >/dev/null 2>&1 || true
"${DIR}/start_demo_tunnel.command" || true
sleep 1

echo "[4/4] 最終狀態"
"${DIR}/status_services.command"

exit 0
