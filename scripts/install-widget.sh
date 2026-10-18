#!/data/data/com.termux/files/usr/bin/bash
# Termux:Widget 에 "문자릴레이" 켜기/끄기 버튼을 만듭니다: bash scripts/install-widget.sh
set -eu
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# ~/.shortcuts/tasks/ 의 스크립트는 터미널 창 없이 백그라운드로 실행됩니다.
mkdir -p "$HOME/.shortcuts/tasks"
chmod 700 "$HOME/.shortcuts" "$HOME/.shortcuts/tasks"
cat > "$HOME/.shortcuts/tasks/문자릴레이" <<SH
#!/data/data/com.termux/files/usr/bin/bash
exec bash "$APP_DIR/scripts/toggle.sh"
SH
chmod 700 "$HOME/.shortcuts/tasks/문자릴레이"
chmod +x "$APP_DIR"/scripts/*.sh

cat <<MSG
위젯 버튼을 만들었습니다: ~/.shortcuts/tasks/문자릴레이
 1. Termux:Widget 앱을 Termux 와 같은 곳(F-Droid 등)에서 설치합니다.
 2. 홈 화면을 길게 눌러 위젯 > Termux:Widget 을 홈 화면에 놓습니다.
 3. 목록의 '문자릴레이' 를 누를 때마다 켜짐/꺼짐이 바뀌고 아래에 잠깐 표시됩니다.
    (목록에 안 보이면 위젯의 새로고침 버튼을 누르세요)
MSG
