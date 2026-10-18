#!/data/data/com.termux/files/usr/bin/bash
# Termux 설치 스크립트: bash scripts/install.sh
set -eu

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONF_DIR="$HOME/.config/mail2sms"

echo "== 패키지 설치 (python, termux-api)"
pkg update -y
pkg install -y python termux-api

echo "== 설정 폴더: $CONF_DIR"
mkdir -p "$CONF_DIR"
chmod 700 "$CONF_DIR"
if [ ! -f "$CONF_DIR/config.toml" ]; then
    cp "$APP_DIR/config.example.toml" "$CONF_DIR/config.toml"
    echo "   config.toml 을 만들었습니다. 꼭 편집하세요: nano $CONF_DIR/config.toml"
fi
chmod 600 "$CONF_DIR/config.toml"

if [ ! -f "$CONF_DIR/app_password" ]; then
    read -r -s -p "Gmail 앱 비밀번호 16자를 붙여넣고 Enter (화면에 안 보임, 나중에 하려면 그냥 Enter): " PW
    echo
    if [ -n "$PW" ]; then
        (umask 077; printf '%s\n' "$PW" > "$CONF_DIR/app_password")
        echo "   $CONF_DIR/app_password 에 저장했습니다 (권한 600)."
    fi
    unset PW
fi
[ -f "$CONF_DIR/app_password" ] && chmod 600 "$CONF_DIR/app_password"

chmod +x "$APP_DIR"/scripts/*.sh

echo "== Termux:Boot 시작 스크립트 설치"
mkdir -p "$HOME/.termux/boot"
sed "s#__APP_DIR__#$APP_DIR#g" "$APP_DIR/termux/boot-start-mail2sms.sh" \
    > "$HOME/.termux/boot/start-mail2sms"
chmod 700 "$HOME/.termux/boot/start-mail2sms"

cat <<MSG

설치 끝. 남은 일:
 1. config.toml 편집:   nano $CONF_DIR/config.toml
 2. 설정 확인:          python $APP_DIR/mail2sms.py --check
 3. 문자 권한 확인:     termux-sms-send -n 받는번호 테스트
    (처음 한 번 Termux:API 의 SMS 권한 허용 창이 뜹니다)
 4. 실행:               bash $APP_DIR/scripts/start.sh
 5. 안드로이드 설정 > 애플리케이션 > Termux, Termux:API, Termux:Boot
    > 배터리 > '제한 없음'(배터리 최적화 해제)로 바꾸세요.
 6. Termux:Boot 앱을 한 번 열어 두면 재부팅 후 자동으로 시작됩니다.
MSG
