#!/data/data/com.termux/files/usr/bin/bash
# 백그라운드로 mail2sms 를 시작합니다 (이미 실행 중이면 아무것도 안 함).
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE="$HOME/.cache/mail2sms.pid"
mkdir -p "$HOME/.cache" "$APP_DIR/logs"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "이미 실행 중입니다 (pid $(cat "$PIDFILE"))."
    exit 0
fi

nohup bash "$APP_DIR/scripts/run.sh" >>"$APP_DIR/logs/runner.log" 2>&1 &
echo $! > "$PIDFILE"
# 실행 중임을 알림창에 띄우고, 알림의 '끄기' 버튼으로 끌 수 있게 합니다.
termux-notification --id mail2sms --ongoing --priority low \
    --title "문자 릴레이 실행 중" --content "메일을 확인해 문자를 보냅니다" \
    --button1 "끄기" --button1-action "bash $APP_DIR/scripts/stop.sh" 2>/dev/null || true
echo "시작했습니다 (pid $!). 로그: tail -f $APP_DIR/logs/mail2sms.log"
