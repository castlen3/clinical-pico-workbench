#!/bin/zsh
set -u

DIR="$(cd "$(dirname "$0")" && pwd)"

choose_main_action() {
  local summary="$1"
  osascript <<APPLESCRIPT
set options to {"啟動全部（9999 + 8790 + Tunnel）", "只啟動主版（9999）", "只啟動 Demo（8790 + Tunnel）", "停止全部", "查看狀態", "快速修復", "離開"}
set chosen to choose from list options with prompt "Clinical PICO 控制中心\n\n目前狀態：${summary}" default items {"查看狀態"} OK button name "執行" cancel button name "離開"
if chosen is false then
  return "__EXIT__"
end if
return item 1 of chosen
APPLESCRIPT
}

choose_close_mode() {
  osascript <<'APPLESCRIPT'
set options to {"關閉目前控制中心視窗", "關閉所有 Terminal 視窗", "不關閉視窗"}
set chosen to choose from list options with prompt "已停止全部服務，要不要順便關閉視窗？" default items {"關閉目前控制中心視窗"} OK button name "確定" cancel button name "略過"
if chosen is false then
  return "不關閉視窗"
end if
return item 1 of chosen
APPLESCRIPT
}

close_current_terminal_window() {
  (sleep 0.4; osascript <<'APPLESCRIPT'
tell application "Terminal"
  if (count of windows) > 0 then
    close front window saving no
  end if
end tell
APPLESCRIPT
) >/dev/null 2>&1 &
}

close_all_terminal_windows() {
  (sleep 0.4; osascript <<'APPLESCRIPT'
tell application "Terminal"
  repeat with w in (every window)
    close w saving no
  end repeat
end tell
APPLESCRIPT
) >/dev/null 2>&1 &
}

while true; do
  SUMMARY="$("${DIR}/status_services.command" --summary 2>/dev/null || echo '狀態讀取失敗')"
  MENU_RESULT="$(choose_main_action "$SUMMARY")"

  if [[ "$MENU_RESULT" == "__EXIT__" || "$MENU_RESULT" == "離開" ]]; then
    echo "已離開控制中心"
    exit 0
  fi

  echo "選擇：$MENU_RESULT"

  case "$MENU_RESULT" in
    "啟動全部（9999 + 8790 + Tunnel）")
      PICO_KEEP_WINDOW=1 "${DIR}/start_clinical_pico.command"
      "${DIR}/start_lite_demo.command"
      "${DIR}/start_demo_tunnel.command"
      ;;
    "只啟動主版（9999）")
      PICO_KEEP_WINDOW=1 "${DIR}/start_clinical_pico.command"
      ;;
    "只啟動 Demo（8790 + Tunnel）")
      "${DIR}/start_lite_demo.command"
      "${DIR}/start_demo_tunnel.command"
      ;;
    "停止全部")
      "${DIR}/stop_demo_tunnel.command"
      "${DIR}/stop_lite_demo.command"
      PICO_KEEP_WINDOW=1 "${DIR}/stop_clinical_pico.command"

      CLOSE_MODE="$(choose_close_mode)"
      echo "視窗處理：$CLOSE_MODE"
      case "$CLOSE_MODE" in
        "關閉目前控制中心視窗")
          close_current_terminal_window
          exit 0
          ;;
        "關閉所有 Terminal 視窗")
          close_all_terminal_windows
          exit 0
          ;;
        *)
          ;;
      esac
      ;;
    "查看狀態")
      ;;
    "快速修復")
      "${DIR}/recover_services.command"
      ;;
    *)
      echo "未知選項"
      ;;
  esac

  echo
  "${DIR}/status_services.command"
  echo
  echo "（控制中心持續待命，可繼續選下一個動作）"
  echo
 done

exit 0
