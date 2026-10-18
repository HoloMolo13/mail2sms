#!/data/data/com.termux/files/usr/bin/bash
# 감시 루프: mail2sms 가 죽으면 30초 뒤 다시 띄웁니다. 직접 쓰기보다 start.sh 를 쓰세요.
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONF="${MAIL2SMS_CONFIG:-$HOME/.config/mail2sms/config.toml}"

termux-wake-lock
while true; do
    python "$APP_DIR/mail2sms.py" --config "$CONF"
    code=$?
    if [ "$code" -eq 2 ]; then
        echo "$(date '+%F %T') 설정 오류로 중단합니다. config.toml 을 확인하세요." >&2
        exit 2
    fi
    sleep 30
done
