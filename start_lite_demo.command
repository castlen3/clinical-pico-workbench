#!/bin/zsh
set -u

DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="8790"
HOST="127.0.0.1"
URL="http://localhost:${PORT}"
HEALTH_URL="http://localhost:${PORT}/api/health"
PID_FILE="${DIR}/.pico_demo_server.pid"
LOG_FILE="${DIR}/.pico_demo_server.log"
DEFAULT_MODEL="google/gemini-2.5-flash-lite"

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

if [[ -f "$PID_FILE" ]]; then
  EXISTING_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  stop_pid "$EXISTING_PID"
  rm -f "$PID_FILE"
fi

PORT_PIDS="$(/usr/sbin/lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "$PORT_PIDS" ]]; then
  for pid in ${(f)PORT_PIDS}; do
    stop_pid "$pid"
  done
fi

printf "請輸入 Demo 用 OpenRouter API Key（輸入時不顯示）: "
if [[ -t 0 ]]; then
  DEMO_KEY=""
  stty -echo
  while IFS= read -r -k 1 ch; do
    if [[ "$ch" == $'\n' || "$ch" == $'\r' ]]; then
      break
    fi
    if [[ "$ch" == $'\177' || "$ch" == $'\b' ]]; then
      if [[ -n "$DEMO_KEY" ]]; then
        DEMO_KEY="${DEMO_KEY[1,-2]}"
        printf "\b \b"
      fi
      continue
    fi
    DEMO_KEY+="$ch"
    printf "o"
  done
  stty echo
  printf "\n"
else
  read -r DEMO_KEY
fi

if [[ -z "${DEMO_KEY// /}" ]]; then
  echo "未輸入 API Key，已取消啟動。"
  exit 1
fi

cd "$DIR" || exit 1
: >"$LOG_FILE"

nohup /usr/bin/env \
  DEMO_OPENROUTER_API_KEY="$DEMO_KEY" \
  DEMO_OPENROUTER_MODEL="$DEFAULT_MODEL" \
  DEMO_DAILY_RUN_LIMIT="10" \
  DEMO_MAX_TOKENS_FINAL="5200" \
  /usr/bin/python3 "$DIR/server_demo.py" --host "$HOST" --port "$PORT" \
  >"$LOG_FILE" 2>&1 &

NEW_PID="$!"
echo "$NEW_PID" >"$PID_FILE"

sleep 1
if is_running "$NEW_PID"; then
  HEALTH_OK="0"
  for _ in {1..12}; do
    code="$(/usr/bin/curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL" || true)"
    if [[ "$code" == "200" ]]; then
      HEALTH_OK="1"
      break
    fi
    sleep 0.25
  done

  if [[ "$HEALTH_OK" == "1" ]]; then
    nohup /usr/bin/open "$URL" >/dev/null 2>&1 &
    echo "Lite Demo 已啟動：$URL"
  else
    stop_pid "$NEW_PID"
    rm -f "$PID_FILE"
    echo "Lite Demo 啟動失敗（API 健康檢查未通過），請查看 ${LOG_FILE}"
  fi
else
  rm -f "$PID_FILE"
  echo "Lite Demo 啟動失敗，請查看 ${LOG_FILE}"
fi

unset DEMO_KEY
exit 0
