#!/data/data/com.termux/files/usr/bin/bash
# mail2sms 를 멈추고 wake lock 을 풉니다.
PIDFILE="$HOME/.cache/mail2sms.pid"
if [ -f "$PIDFILE" ]; then
    PID="$(cat "$PIDFILE")"
    pkill -P "$PID" 2>/dev/null
    kill "$PID" 2>/dev/null
    rm -f "$PIDFILE"
    echo "멈췄습니다."
else
    echo "실행 중이 아닙니다."
fi
termux-wake-unlock
termux-notification-remove mail2sms 2>/dev/null || true
