#!/data/data/com.termux/files/usr/bin/sh
# Termux:Boot 가 부팅 때 실행합니다. install.sh 가 ~/.termux/boot/start-mail2sms 로 복사합니다.
termux-wake-lock
sleep 30   # 네트워크가 잡힐 시간
bash "__APP_DIR__/scripts/start.sh"
