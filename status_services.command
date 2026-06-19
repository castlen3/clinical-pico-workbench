#!/bin/zsh
set -u

DIR="$(cd "$(dirname "$0")" && pwd)"
MAIN_HEALTH="http://127.0.0.1:9999/api/health"
DEMO_HEALTH="http://127.0.0.1:8790/api/health"
MAIN_PID_FILE="${DIR}/.pico_server.pid"
DEMO_PID_FILE="${DIR}/.pico_demo_server.pid"
TUNNEL_PID_FILE="${DIR}/.demo_tunnel.pid"
TUNNEL_LOG_FILE="${DIR}/.demo_tunnel.log"

is_pid_running() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1
}

read_pid() {
  local file="$1"
  if [[ -f "$file" ]]; then
    cat "$file" 2>/dev/null || true
  fi
}

http_code() {
  local url="$1"
  /usr/bin/curl -s -o /dev/null -w "%{http_code}" --max-time 4 "$url" || true
}

https_code_with_dns_fallback() {
  local url="$1"
  local code
  code="$(http_code "$url")"
  if [[ "$code" == "200" ]]; then
    echo "$code"
    return 0
  fi

  local host path ip
  host="${url#https://}"
  host="${host%%/*}"
  path="${url#https://$host}"
  [[ -z "$path" ]] && path="/"

  ip="$(/usr/bin/dig +short "$host" @1.1.1.1 2>/dev/null | /usr/bin/head -n 1)"
  if [[ -n "$ip" ]]; then
    code="$(/usr/bin/curl -s -o /dev/null -w "%{http_code}" --max-time 5 --resolve "${host}:443:${ip}" "https://${host}${path}" || true)"
  fi
  echo "${code:-000}"
}

status_text() {
  local ok="$1"
  if [[ "$ok" == "1" ]]; then
    echo "UP"
  else
    echo "DOWN"
  fi
}

MAIN_PID="$(read_pid "$MAIN_PID_FILE")"
DEMO_PID="$(read_pid "$DEMO_PID_FILE")"
TUNNEL_PID="$(read_pid "$TUNNEL_PID_FILE")"

MAIN_PID_UP="0"
DEMO_PID_UP="0"
TUNNEL_PID_UP="0"

is_pid_running "$MAIN_PID" && MAIN_PID_UP="1"
is_pid_running "$DEMO_PID" && DEMO_PID_UP="1"
is_pid_running "$TUNNEL_PID" && TUNNEL_PID_UP="1"

MAIN_CODE="$(http_code "$MAIN_HEALTH")"
DEMO_CODE="$(http_code "$DEMO_HEALTH")"

MAIN_HTTP_UP="0"
DEMO_HTTP_UP="0"
[[ "$MAIN_CODE" == "200" ]] && MAIN_HTTP_UP="1"
[[ "$DEMO_CODE" == "200" ]] && DEMO_HTTP_UP="1"

TUNNEL_URL=""
if [[ "$TUNNEL_PID_UP" == "1" ]] && [[ -f "$TUNNEL_LOG_FILE" ]]; then
  TUNNEL_URL="$(grep -Eo 'https://[-a-z0-9]+\.trycloudflare\.com' "$TUNNEL_LOG_FILE" | tail -n 1 || true)"
fi

TUNNEL_EXT_CODE="-"
TUNNEL_EXT_CODE_FALLBACK="-"
TUNNEL_EXT_UP="0"
TUNNEL_STATE="DOWN"
if [[ -n "$TUNNEL_URL" ]]; then
  # 以系統 DNS 的直接連線作為正式狀態，避免 fallback 造成誤判。
  TUNNEL_EXT_CODE="$(http_code "$TUNNEL_URL/api/health")"
  [[ "$TUNNEL_EXT_CODE" == "200" ]] && TUNNEL_EXT_UP="1"
  TUNNEL_EXT_CODE_FALLBACK="$(https_code_with_dns_fallback "$TUNNEL_URL/api/health")"
  if [[ "$TUNNEL_EXT_CODE" == "200" ]]; then
    TUNNEL_STATE="UP"
  elif [[ "$TUNNEL_EXT_CODE_FALLBACK" == "200" ]]; then
    TUNNEL_STATE="DNS_ISSUE"
  else
    TUNNEL_STATE="DOWN"
  fi
fi

NOW="$(date '+%Y-%m-%d %H:%M:%S')"

if [[ "${1:-}" == "--summary" ]]; then
  echo "MAIN=$(status_text "$MAIN_HTTP_UP") DEMO=$(status_text "$DEMO_HTTP_UP") TUNNEL=${TUNNEL_STATE} URL=${TUNNEL_URL:-none}"
  exit 0
fi

cat <<EOF
================ Clinical PICO Services ================
Time: $NOW

[Main 9999]
- Process: $(status_text "$MAIN_PID_UP") (pid: ${MAIN_PID:-none})
- Health : $(status_text "$MAIN_HTTP_UP") (code: ${MAIN_CODE:-000})
- URL    : http://127.0.0.1:9999

[Demo 8790]
- Process: $(status_text "$DEMO_PID_UP") (pid: ${DEMO_PID:-none})
- Health : $(status_text "$DEMO_HTTP_UP") (code: ${DEMO_CODE:-000})
- URL    : http://127.0.0.1:8790

[Tunnel]
- Process: $(status_text "$TUNNEL_PID_UP") (pid: ${TUNNEL_PID:-none})
- URL    : ${TUNNEL_URL:-none}
- Public : ${TUNNEL_STATE} (direct code: ${TUNNEL_EXT_CODE})
- Note   : dns-fallback code: ${TUNNEL_EXT_CODE_FALLBACK}
=========================================================
EOF

exit 0
