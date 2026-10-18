# mail2sms — Gmail로 메일을 보내면 폰이 문자를 대신 보내 줍니다

휴대폰을 가지고 들어갈 수 없는 곳에 있을 때, 회사 PC에서 내 Gmail로 메일을 보내면
두고 온 안드로이드 폰의 Termux가 그 메일을 읽고 **정해진 번호 한 곳으로만** 문자를 보냅니다.

```
회사 PC ──메일──▶ 내 Gmail ◀──IMAP(60초마다)── 폰의 Termux ──termux-sms-send──▶ 아내 폰
```

- Python 표준 라이브러리만 씁니다(추가 패키지 없음, AI/API 비용 없음).
- 처음에는 `dry_run = true` 라서 **문자를 실제로 보내지 않습니다.** 충분히 시험한 뒤 끄세요.

## 보내는 방법

받는 주소: 폰에 설정한 Gmail 주소
제목: `[접두어-비밀코드] 보낼 문구`

```
제목: [집-k7Qm29xa] 회의가 길어져서 8시쯤 나가요
```

- 제목의 태그 뒤 문구가 문자로 갑니다. 제목이 `[집-k7Qm29xa]` 뿐이면 **본문 첫 줄**을 보냅니다.
- 문구는 `max_chars`(기본 70자)에서 잘립니다.
- 받는 번호는 설정 파일에 고정돼 있어 메일로 바꿀 수 없습니다.

## 안전장치

기본 설정에서는 **어느 주소에서 보내든 제목만 맞으면** 처리합니다. 다음을 모두 만족해야 문자를 보냅니다.

1. 제목이 `[접두어-비밀코드]` 로 정확히 시작함
2. (선택) `allowed_senders` 를 채웠다면 보낸 사람이 그 목록에 있음
3. (선택) `require_dmarc_pass = true` 이면 Gmail이 확인한 DMARC 결과가 pass
4. 시간당 최대 `max_per_hour` 건, 전송 사이 최소 `min_interval_sec` 초

> **보낸 사람 제한 없이 쓸 때는 비밀 코드가 유일한 열쇠입니다.**
> - `allowed_senders` 가 비어 있으면 비밀 코드는 **8자 이상**이어야 시작됩니다.
>   영문 대소문자와 숫자를 섞어 12자 정도로 만드는 것을 권합니다(예: `k7Qm29xaP4`).
> - 메일 제목은 받는 사람·회사 메일 서버·보낸편지함에 그대로 남습니다. 다른 사람이 볼 수 있는
>   화면이나 공유 메일함에서는 보내지 말고, 코드가 노출됐다고 생각되면 바로 바꾸세요.
> - 메일의 보낸 사람 주소는 쉽게 위조할 수 있으므로 `allowed_senders` 는 보조 수단일 뿐입니다.

처리 결과:

| 메일 | 동작 |
|---|---|
| 태그가 없는 메일, (목록이 있을 때) 허용되지 않은 주소의 메일 | 건드리지 않음(읽지 않음 상태 그대로) |
| 비밀 코드가 틀림 | 문자 안 보냄, 읽음 표시, 로그에 경고. "거부됨" 회신은 `allowed_senders` 를 채웠을 때만 |
| 전송 제한 초과 / 문구 없음 | 문자 안 보냄, 읽음 표시, 로그에 경고, (선택) "거부됨" 회신 |
| 조건 통과 | 문자 보냄, 읽음 표시, (선택) 라벨, (선택) "보냈음" 회신 |

보낸 사람 제한이 없을 때 코드가 틀린 메일에 회신하지 않는 것은, 위조된 모르는 주소로 메일이
나가지 않게 하려는 것입니다. 회신 메일 제목에는 비밀 코드가 들어가지 않습니다. 코드가 틀린 메일이 온다면 누군가 시도하고 있다는 뜻이니
코드를 바꾸세요.

## 1. Gmail 준비: 2단계 인증과 앱 비밀번호

일반 Gmail 비밀번호로는 IMAP 로그인이 안 됩니다. **앱 비밀번호**(16자)를 만들어야 하고,
그러려면 먼저 2단계 인증이 켜져 있어야 합니다.

1. PC 브라우저에서 <https://myaccount.google.com> → **보안**
2. **2단계 인증**을 켭니다(문자나 Google OTP 앱 등으로 설정).
3. <https://myaccount.google.com/apppasswords> 로 갑니다
   (보안 페이지 검색창에 "앱 비밀번호"를 쳐도 됩니다).
4. 앱 이름에 `mail2sms` 를 입력하고 **만들기** → 노란 상자의 16자 비밀번호를 복사합니다.
   이 창을 닫으면 다시 볼 수 없으니, 폰에 넣기 전까지만 잠깐 보관하세요.
5. Gmail 웹 → 설정(톱니) → **모든 설정 보기** → **전달 및 POP/IMAP** → IMAP이
   사용 설정되어 있는지 확인합니다(요즘 계정은 기본으로 켜져 있습니다).

앱 비밀번호는 Gmail 전체에 접근할 수 있습니다. 폰을 잃어버리거나 그만 쓰게 되면
같은 페이지에서 삭제하세요.

> 가능하면 이 용도로 **새 Gmail 계정**을 하나 만들어 쓰는 편이 더 안전합니다.

## 2. 폰 준비

F-Droid(또는 GitHub 릴리스)에서 **같은 곳의** 앱을 설치합니다. Play 스토어판 Termux와
F-Droid판 Termux:API는 서로 맞지 않습니다.

- Termux (이미 설치됨)
- **Termux:API** — 문자 전송에 필요
- **Termux:Boot** — 재부팅 후 자동 시작(선택)

### 배터리 최적화 해제 (중요)

안드로이드는 화면이 꺼진 앱을 잠재웁니다. 안 하면 몇 시간 뒤 멈춥니다.

- 설정 → 애플리케이션 → **Termux** → 배터리 → **제한 없음**(또는 "최적화 안 함")
- **Termux:API**, **Termux:Boot** 도 같은 방법으로
- 삼성: 설정 → 배터리 → 백그라운드 사용 제한 → "절전 상태로 만들지 않을 앱"에 Termux 추가
- 시작 스크립트가 `termux-wake-lock` 을 걸어 둡니다. 알림창의 Termux 알림에
  "wake lock held" 가 보이면 정상입니다.

## 3. 코드 옮기기

PC의 이 폴더를 폰의 `~/mail2sms` 로 옮깁니다. 방법은 아무거나:

- 비공개 GitHub 저장소에 올린 뒤 Termux에서
  `pkg install git && git clone <주소> ~/mail2sms`
- USB로 `Download/mail2sms` 에 복사한 뒤 Termux에서
  `termux-setup-storage && cp -r ~/storage/downloads/mail2sms ~/mail2sms`

설정 파일과 비밀번호는 저장소에 들어가지 않습니다(`config.example.toml` 만 들어 있음).

## 4. 설치와 설정 (Termux에서)

```bash
cd ~/mail2sms
bash scripts/install.sh
```

스크립트가 하는 일: `pkg install python termux-api`, `~/.config/mail2sms/config.toml` 생성,
앱 비밀번호 입력받아 `~/.config/mail2sms/app_password` 에 권한 600으로 저장,
`~/.termux/boot/start-mail2sms` 설치.

설정 파일을 직접 고칩니다.

```bash
nano ~/.config/mail2sms/config.toml
```

꼭 바꿀 항목:

| 항목 | 예 |
|---|---|
| `imap.user` | 폰이 확인할 Gmail 주소 |
| `filter.subject_prefix` | `집` |
| `filter.secret_code` | 추측하기 어려운 8자 이상 (예: `k7Qm29xaP4`) |
| `sms.recipient` | 받는 사람 번호 |

선택 항목: `filter.allowed_senders` 에 보낼 주소를 넣으면 그 주소에서 온 메일만 처리합니다
(이때는 비밀 코드가 4자 이상이면 됩니다). 기본값 `[]` 은 주소 제한 없음입니다.

예시 값이 남아 있으면 프로그램이 시작을 거부합니다.
앱 비밀번호는 파일 대신 환경변수 `MAIL2SMS_APP_PASSWORD` 로 줄 수도 있습니다.
비밀번호 파일을 나중에 만든다면:

```bash
(umask 077; nano ~/.config/mail2sms/app_password)
chmod 600 ~/.config/mail2sms/app_password
```

## 5. 시험

```bash
# 설정과 비밀번호 확인
python ~/mail2sms/mail2sms.py --check

# 문자 권한 확인(처음 한 번 권한 창이 뜹니다) — 내 번호로 시험하세요
termux-sms-send -n 내번호 테스트

# 한 번만 메일 확인 (dry_run=true 이면 문자 대신 로그만)
python ~/mail2sms/mail2sms.py --once
```

회사 PC에서 `[집-비밀코드] 테스트` 메일을 보내고 `--once` 를 돌려 로그에
`[dry-run] would run` 이 찍히는지 봅니다. 처음엔 `recipient` 를 **내 번호**로 두고
`dry_run = false` 로 실제 전송까지 시험한 뒤, 받는 번호를 바꾸는 것을 권합니다.

## 6. 실행 / 중지

```bash
bash ~/mail2sms/scripts/start.sh    # 백그라운드 시작(죽으면 30초 뒤 다시 시작)
bash ~/mail2sms/scripts/stop.sh     # 중지 + wake lock 해제
tail -f ~/mail2sms/logs/mail2sms.log
```

Termux:Boot 앱을 한 번 열어 두면 재부팅 후 자동으로 시작됩니다.

## 설정 항목 전체

`config.example.toml` 에 주석으로 설명되어 있습니다. 주요 선택 기능:

- `gmail.label` — 처리한 메일에 Gmail 라벨(영문 이름)
- `reply.enabled` — 결과를 보낸 사람에게 회신("보냈음" / "거부됨" / "전송 실패").
  같은 앱 비밀번호로 smtp.gmail.com 을 씁니다.
- `filter.require_dmarc_pass` — 회사 메일이 DMARC를 통과한다면 켜 두세요.
  Gmail에서 받은 메일 → ⋮ → **원본 보기** 에서 `DMARC: PASS` 인지 확인할 수 있습니다.

## 문제 해결

- `설정 오류: ...` — 메시지에 나온 항목을 고치세요. 스크립트는 설정 오류면 재시작하지 않습니다.
- `imap error: ... AUTHENTICATIONFAILED` — 앱 비밀번호가 틀렸거나 2단계 인증이 꺼졌습니다.
- `termux-sms-send not found` — `pkg install termux-api` 와 Termux:API 앱 설치를 확인하세요.
- 문자가 안 가고 명령이 멈춤 — Termux:API 앱의 SMS 권한(설정 → 애플리케이션 → Termux:API → 권한)을 확인하세요.
- 회사 메일에서 보낸 메일이 Gmail 스팸함으로 가면 처리되지 않습니다. Gmail에서 그 주소로
  필터를 만들어 "스팸으로 보내지 않음"을 지정하세요.
- 몇 시간 뒤 멈춤 — 배터리 최적화 해제를 다시 확인하세요.

## 개발 (PC)

```bash
python -m venv .venv
.venv/Scripts/python -m pip install pytest
.venv/Scripts/python -m pytest -q
```

테스트는 IMAP, SMTP, `subprocess` 를 가짜 객체로 바꿔 돌리므로 실제 메일이나 문자는 오가지 않습니다.
