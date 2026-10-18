#!/data/data/com.termux/files/usr/bin/bash
# 켜져 있으면 끄고, 꺼져 있으면 켭니다. Termux:Widget 버튼이 이 스크립트를 부릅니다.
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE="$HOME/.cache/mail2sms.pid"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    bash "$APP_DIR/scripts/stop.sh" >/dev/null
    termux-toast -g bottom "문자 릴레이 꺼짐" 2>/dev/null
else
    bash "$APP_DIR/scripts/start.sh" >/dev/null
    termux-toast -g bottom "문자 릴레이 켜짐" 2>/dev/null
fi
exit 0
