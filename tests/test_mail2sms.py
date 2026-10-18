import os
import smtplib
import subprocess
import sys
import unicodedata
from email.message import EmailMessage
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mail2sms as m  # noqa: E402

OTHER = "stranger@evil.com"


def make_cfg(**kw):
    base = dict(
        imap_user="relay@gmail.com",
        allowed_senders=frozenset({"boss.me@work.co.kr"}),
        subject_prefix="집",
        secret_code="4821",
        recipient="01012345678",
        dry_run=False,
        max_chars=20,
        max_per_hour=3,
        min_interval_sec=60,
    )
    base.update(kw)
    return m.Config(**base)


ALLOWED = "boss.me@work.co.kr"


def raw_mail(subject, body="", sender=ALLOWED, html=False, extra=None):
    msg = EmailMessage()
    msg["From"] = f"Boss <{sender}>"
    msg["To"] = "relay@gmail.com"
    msg["Subject"] = subject
    msg["Message-ID"] = "<abc@work.co.kr>"
    for k, v in (extra or {}).items():
        msg[k] = v
    if html:
        msg.set_content(body, subtype="html")
    else:
        msg.set_content(body)
    return msg.as_bytes()


# ---------------------------------------------------------------- parsing


def test_parse_basic_korean_subject_and_body():
    p = m.parse_message(raw_mail("[집-4821] 오늘 늦어요", "\n\n  첫 줄입니다\n둘째 줄"))
    assert p.sender == ALLOWED
    assert p.subject == "[집-4821] 오늘 늦어요"
    assert p.first_line == "첫 줄입니다"
    assert p.message_id == "<abc@work.co.kr>"


def test_parse_encoded_subject_header():
    from email.header import Header

    enc = Header("[집-4821] 오늘 늦어요", "utf-8").encode()
    assert enc.startswith("=?utf-8?")
    raw = (
        b"From: Boss <BOSS.me@Work.co.kr>\r\n"
        + b"Subject: " + enc.encode() + b"\r\n"
        + b"Content-Type: text/plain; charset=utf-8\r\n\r\nbody\r\n"
    )
    p = m.parse_message(raw)
    assert p.sender == ALLOWED  # lower-cased
    assert p.subject == "[집-4821] 오늘 늦어요"


def test_parse_html_body_first_line():
    p = m.parse_message(raw_mail("[집-4821]", "<html><body><p>회의 &amp; 야근</p><p>둘</p></body></html>", html=True))
    assert p.first_line == "회의 & 야근"


def test_parse_nfd_subject_is_normalized():
    nfd = unicodedata.normalize("NFD", "[집-4821] 늦어요")
    p = m.parse_message(raw_mail(nfd))
    assert p.subject == "[집-4821] 늦어요"


def test_parse_counts_multiple_from_addresses():
    raw = b"From: a@x.com, b@y.com\r\nSubject: hi\r\n\r\nx\r\n"
    assert m.parse_message(raw).from_count == 2


# ---------------------------------------------------------------- filter


def decide(subject, body="", **kw):
    cfg = make_cfg(**kw.pop("cfg", {}))
    return m.evaluate(m.parse_message(raw_mail(subject, body, **kw)), cfg)


def test_accept_subject_rest():
    d = decide("[집-4821] 오늘 늦어요")
    assert d.action == m.ACCEPT and d.text == "오늘 늦어요"


def test_accept_uses_body_when_subject_rest_empty():
    d = decide("[집-4821]", "지금 출발\n추가")
    assert d.action == m.ACCEPT and d.text == "지금 출발"


def test_truncates_to_max_chars():
    d = decide("[집-4821] " + "가" * 50)
    assert d.text == "가" * 20


def test_ignore_unknown_sender():
    assert decide("[집-4821] 늦어요", sender=OTHER).action == m.IGNORE


def test_ignore_allowed_sender_without_tag():
    assert decide("그냥 메모").action == m.IGNORE
    assert decide("Re: [집-4821] 늦어요").action == m.IGNORE


def test_reject_wrong_code():
    d = decide("[집-1111] 늦어요")
    assert d.action == m.REJECT and "secret" in d.reason


def test_reject_code_prefix_only():
    # "[집-482]" must not match "[집-4821]"
    assert decide("[집-482] 늦어요").action == m.REJECT


def test_reject_empty_message():
    assert decide("[집-4821]", "").action == m.REJECT


def test_dmarc_required():
    cfg = {"require_dmarc_pass": True}
    assert decide("[집-4821] 늦어요", cfg=cfg).action == m.REJECT
    ok = decide(
        "[집-4821] 늦어요",
        cfg=cfg,
        extra={"Authentication-Results": "mx.google.com; dkim=pass; spf=pass; dmarc=pass (p=NONE)"},
    )
    assert ok.action == m.ACCEPT


def test_control_chars_removed():
    d = decide("[집-4821] 줄\t바꿈")
    assert d.text == "줄 바꿈"


# ---------------------------------------------------------------- rate limit


def test_rate_limiter_min_interval_and_hourly(tmp_path):
    rl = m.RateLimiter(max_per_hour=3, min_interval_sec=60, state_file=tmp_path / "s.json")
    t = 1_000_000.0
    assert rl.check(t)[0]
    rl.record(t)
    assert not rl.check(t + 30)[0]  # too soon
    assert rl.check(t + 61)[0]
    rl.record(t + 61)
    rl.record(t + 122)
    ok, why = rl.check(t + 200)
    assert not ok and "hourly" in why
    assert rl.check(t + 3601)[0]  # first one expired


def test_rate_limiter_persists(tmp_path):
    f = tmp_path / "s.json"
    rl = m.RateLimiter(1, 0, f)
    rl.record(100.0)
    rl2 = m.RateLimiter(1, 0, f)
    assert not rl2.check(200.0)[0]


def test_rate_limiter_corrupt_state(tmp_path):
    f = tmp_path / "s.json"
    f.write_text("not json")
    assert m.RateLimiter(1, 0, f).check(0.0)[0]


# ---------------------------------------------------------------- sms command


def test_build_sms_command():
    assert m.build_sms_command("01012345678", "늦어요") == [
        "termux-sms-send", "-n", "01012345678", "늦어요",
    ]


def test_build_sms_command_leading_dash_is_not_an_option():
    cmd = m.build_sms_command("01012345678", "-n 장난")
    assert cmd[3] == " -n 장난"


def test_build_sms_command_rejects_bad_number():
    with pytest.raises(ValueError):
        m.build_sms_command("0101234; rm -rf", "x")


class Runner:
    def __init__(self, returncode=0, exc=None):
        self.calls = []
        self.returncode = returncode
        self.exc = exc

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(cmd, self.returncode, "", "boom")


def test_send_sms_runs_command_without_shell():
    r = Runner()
    ok, _ = m.send_sms(make_cfg(), "늦어요", r)
    assert ok
    cmd, kw = r.calls[0]
    assert cmd == ["termux-sms-send", "-n", "01012345678", "늦어요"]
    assert not kw.get("shell")
    assert kw["timeout"] == 60


def test_send_sms_dry_run_does_not_run():
    r = Runner()
    ok, detail = m.send_sms(make_cfg(dry_run=True), "늦어요", r)
    assert ok and detail == "dry-run" and r.calls == []


def test_send_sms_failures():
    assert not m.send_sms(make_cfg(), "x", Runner(returncode=1))[0]
    assert not m.send_sms(make_cfg(), "x", Runner(exc=FileNotFoundError()))[0]
    assert not m.send_sms(make_cfg(), "x", Runner(exc=subprocess.TimeoutExpired("x", 1)))[0]


# ---------------------------------------------------------------- IMAP flow


class FakeIMAP:
    def __init__(self, mails):
        self.mails = dict(mails)  # uid(bytes) -> raw
        self.flags = {u: set() for u in self.mails}
        self.labels = {u: set() for u in self.mails}
        self.commands = []

    # constructor-compatible factory
    def __call__(self, host, port):
        self.host, self.port = host, port
        return self

    def login(self, user, pw):
        self.login_args = (user, pw)
        return "OK", [b"ok"]

    def select(self, mailbox):
        return "OK", [str(len(self.mails)).encode()]

    def response(self, code):
        return code, [b"1"]

    def noop(self):
        return "OK", []

    def logout(self):
        return "BYE", []

    def uid(self, cmd, *args):
        self.commands.append((cmd, args))
        if cmd == "SEARCH":
            assert args[:3] == (None, "UNSEEN", "SINCE")
            uids = [u for u in self.mails if "\\Seen" not in self.flags[u]]
            return "OK", [b" ".join(uids)]
        if cmd == "FETCH":
            uid, what = args
            assert "PEEK" in what  # must not mark unrelated mail read
            return "OK", [(uid + b" (BODY[] {n}", self.mails[uid]), b")"]
        if cmd == "STORE":
            uid, op, val = args
            if op == "+FLAGS":
                self.flags[uid].add(val.strip("()"))
            elif op == "+X-GM-LABELS":
                self.labels[uid].add(val)
            return "OK", []
        raise AssertionError(cmd)


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=None):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, pw):
        pass

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def make_relay(mails, tmp_path, runner=None, **cfg_kw):
    cfg = make_cfg(**cfg_kw)
    imap = FakeIMAP(mails)
    FakeSMTP.sent = []
    relay = m.Relay(
        cfg,
        "pw",
        m.RateLimiter(cfg.max_per_hour, cfg.min_interval_sec, tmp_path / "state.json"),
        imap_factory=imap,
        runner=runner or Runner(),
        smtp_factory=FakeSMTP,
        clock=lambda: 1_700_000_000.0,
    )
    return relay, imap


def test_poll_sends_accepted_and_leaves_others_unread(tmp_path):
    mails = {
        b"1": raw_mail("[집-4821] 오늘 늦어요"),
        b"2": raw_mail("광고", sender=OTHER),
        b"3": raw_mail("[집-4821] 스팸", sender=OTHER),  # forged tag, wrong sender
        b"4": raw_mail("메모"),
    }
    runner = Runner()
    relay, imap = make_relay(mails, tmp_path, runner, gmail_label="mail2sms", reply_enabled=True)
    conn = relay.connect()
    assert imap.login_args == ("relay@gmail.com", "pw")

    assert relay.poll_once(conn) == 1
    assert [c[0] for c in runner.calls] == [["termux-sms-send", "-n", "01012345678", "오늘 늦어요"]]
    assert imap.flags[b"1"] == {"\\Seen"}
    assert imap.labels[b"1"] == {'("mail2sms")'}
    for u in (b"2", b"3", b"4"):
        assert imap.flags[u] == set()
    assert len(FakeSMTP.sent) == 1
    reply = FakeSMTP.sent[0]
    assert reply["To"] == ALLOWED
    assert "보냈음" in str(reply["Subject"])
    assert "4821" not in str(reply["Subject"])  # never leak the code
    assert reply["In-Reply-To"] == "<abc@work.co.kr>"

    # second poll: ignored mails are not fetched again
    imap.commands.clear()
    assert relay.poll_once(conn) == 0
    assert [c for c in imap.commands if c[0] == "FETCH"] == []


def test_wrong_code_marked_read_and_rejection_reply(tmp_path):
    runner = Runner()
    relay, imap = make_relay({b"7": raw_mail("[집-0000] 늦어요")}, tmp_path, runner, reply_enabled=True)
    relay.poll_once(relay.connect())
    assert runner.calls == []
    assert imap.flags[b"7"] == {"\\Seen"}
    assert "거부됨" in str(FakeSMTP.sent[0]["Subject"])


def test_rate_limit_applies_across_mails(tmp_path):
    mails = {str(i).encode(): raw_mail(f"[집-4821] 메시지{i}") for i in range(1, 4)}
    runner = Runner()
    relay, imap = make_relay(mails, tmp_path, runner, min_interval_sec=60)
    assert relay.poll_once(relay.connect()) == 3
    assert len(runner.calls) == 1  # same clock -> 2nd and 3rd hit min interval
    assert all(imap.flags[u] == {"\\Seen"} for u in mails)


def test_sms_failure_still_marks_read_no_retry_storm(tmp_path):
    runner = Runner(returncode=1)
    relay, imap = make_relay({b"1": raw_mail("[집-4821] 늦어요")}, tmp_path, runner, reply_enabled=True)
    relay.poll_once(relay.connect())
    assert imap.flags[b"1"] == {"\\Seen"}
    assert "전송 실패" in str(FakeSMTP.sent[0]["Subject"])
    assert relay.limiter.sent == []


def test_dry_run_flow(tmp_path):
    runner = Runner()
    relay, imap = make_relay({b"1": raw_mail("[집-4821] 늦어요")}, tmp_path, runner, dry_run=True, reply_enabled=True)
    relay.poll_once(relay.connect())
    assert runner.calls == []
    assert imap.flags[b"1"] == {"\\Seen"}
    assert "테스트 모드" in str(FakeSMTP.sent[0]["Subject"])


def test_reply_failure_does_not_crash(tmp_path):
    class BadSMTP(FakeSMTP):
        def login(self, user, pw):
            raise smtplib.SMTPAuthenticationError(535, b"no")

    relay, imap = make_relay({b"1": raw_mail("[집-4821] 늦어요")}, tmp_path, reply_enabled=True)
    relay.smtp_factory = BadSMTP
    assert relay.poll_once(relay.connect()) == 1


def test_run_forever_reconnects_after_error(tmp_path):
    relay, imap = make_relay({}, tmp_path)
    calls = {"n": 0}
    orig = relay.connect

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("network down")
        return orig()

    relay.connect = flaky
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        if len(sleeps) >= 3:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        relay.run_forever(sleep)
    assert calls["n"] == 2
    assert sleeps[0] == 120 and sleeps[1] == 60  # backoff then normal


# ---------------------------------------------------------------- config / password


EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.toml"


def test_example_config_is_rejected_until_edited():
    with pytest.raises(m.ConfigError) as e:
        m.load_config(EXAMPLE)
    msg = str(e.value)
    for key in ("imap.user", "secret_code", "recipient"):
        assert key in msg


def write_config(tmp_path, **repl):
    text = EXAMPLE.read_text(encoding="utf-8")
    text = text.replace('"you@gmail.com"', '"relay@gmail.com"')
    text = text.replace('"CHANGE_ME"', '"k7Qm29xaP4"')
    text = text.replace('"01000000000"', '"010-1234-5678"')
    for a, b in repl.items():
        text = text.replace(a, b)
    p = tmp_path / "config.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_load_edited_config(tmp_path):
    cfg = m.load_config(write_config(tmp_path))
    assert cfg.dry_run is True  # safe default
    assert cfg.recipient == "01012345678"
    assert cfg.allowed_senders == frozenset()  # default: any sender
    assert cfg.tag == "[집-k7Qm29xaP4]"
    assert cfg.poll_interval_sec == 60


def test_password_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv(m.PASSWORD_ENV, "abcd efgh ijkl mnop")
    assert m.load_password(make_cfg()) == "abcdefghijklmnop"


def test_password_from_file(tmp_path, monkeypatch):
    monkeypatch.delenv(m.PASSWORD_ENV, raising=False)
    f = tmp_path / "pw"
    f.write_text("abcd efgh ijkl mnop\n")
    os.chmod(f, 0o600)
    assert m.load_password(make_cfg(password_file=f)) == "abcdefghijklmnop"


@pytest.mark.skipif(os.name != "posix", reason="file modes are POSIX-only")
def test_password_file_too_open(tmp_path, monkeypatch):
    monkeypatch.delenv(m.PASSWORD_ENV, raising=False)
    f = tmp_path / "pw"
    f.write_text("x")
    os.chmod(f, 0o644)
    with pytest.raises(m.ConfigError):
        m.load_password(make_cfg(password_file=f))


def test_password_missing(monkeypatch):
    monkeypatch.delenv(m.PASSWORD_ENV, raising=False)
    with pytest.raises(m.ConfigError):
        m.load_password(make_cfg())


# ---------------------------------------------------------------- open mode (no sender allowlist)

OPEN_CODE = "k7Qm29xaP4"


def test_example_config_has_empty_allowlist_and_no_sender_error():
    with pytest.raises(m.ConfigError) as e:
        m.load_config(EXAMPLE)
    msg = str(e.value)
    assert "allowed_senders" not in msg.replace("allowed_senders 가 비어 있으면", "")


def test_open_mode_requires_long_code(tmp_path):
    with pytest.raises(m.ConfigError) as e:
        m.load_config(write_config(tmp_path, **{'"k7Qm29xaP4"': '"4821"'}))
    assert "8자" in str(e.value)


def test_allowlist_mode_allows_short_code(tmp_path):
    p = write_config(
        tmp_path,
        **{'"k7Qm29xaP4"': '"4821"', "allowed_senders = []": 'allowed_senders = ["Boss.Me@work.co.kr"]'},
    )
    cfg = m.load_config(p)
    assert cfg.allowed_senders == frozenset({"boss.me@work.co.kr"})
    assert cfg.secret_code == "4821"


def test_open_mode_accepts_any_sender_with_code():
    cfg = make_cfg(allowed_senders=frozenset(), secret_code=OPEN_CODE)
    d = m.evaluate(m.parse_message(raw_mail(f"[집-{OPEN_CODE}] 늦어요", sender=OTHER)), cfg)
    assert d.action == m.ACCEPT and d.text == "늦어요"


def test_open_mode_still_ignores_untagged_mail():
    cfg = make_cfg(allowed_senders=frozenset(), secret_code=OPEN_CODE)
    d = m.evaluate(m.parse_message(raw_mail("광고", sender=OTHER)), cfg)
    assert d.action == m.IGNORE


def test_open_mode_wrong_code_no_reply_right_code_reply(tmp_path):
    mails = {
        b"1": raw_mail("[집-guessguess] 장난", sender=OTHER),
        b"2": raw_mail("광고", sender=OTHER),
        b"3": raw_mail(f"[집-{OPEN_CODE}] 오늘 늦어요", sender="anyone@else.org"),
    }
    runner = Runner()
    relay, imap = make_relay(
        mails, tmp_path, runner,
        allowed_senders=frozenset(), secret_code=OPEN_CODE, reply_enabled=True,
    )
    relay.poll_once(relay.connect())
    assert [c[0][3] for c in runner.calls] == ["오늘 늦어요"]
    assert imap.flags[b"1"] == {"\\Seen"}  # wrong code: marked read
    assert imap.flags[b"2"] == set()
    assert imap.flags[b"3"] == {"\\Seen"}
    assert [str(r["To"]) for r in FakeSMTP.sent] == ["anyone@else.org"]
    assert "보냈음" in str(FakeSMTP.sent[0]["Subject"])


def test_allowlist_mode_wrong_code_still_replies(tmp_path):
    relay, imap = make_relay({b"1": raw_mail("[집-0000] x")}, tmp_path, reply_enabled=True)
    relay.poll_once(relay.connect())
    assert len(FakeSMTP.sent) == 1 and "거부됨" in str(FakeSMTP.sent[0]["Subject"])


def test_open_mode_rate_limit_still_applies(tmp_path):
    mails = {str(i).encode(): raw_mail(f"[집-{OPEN_CODE}] m{i}", sender=f"u{i}@x.com") for i in range(1, 4)}
    runner = Runner()
    relay, _ = make_relay(mails, tmp_path, runner, allowed_senders=frozenset(), secret_code=OPEN_CODE)
    relay.poll_once(relay.connect())
    assert len(runner.calls) == 1


# ---------------------------------------------------------------- gmail label


def test_system_label_rejected_in_config(tmp_path):
    p = write_config(tmp_path, **{'label = ""': 'label = "Sent"'})
    with pytest.raises(m.ConfigError) as e:
        m.load_config(p)
    assert "gmail.label" in str(e.value)


def test_label_error_does_not_crash(tmp_path):
    class LabelRefusingIMAP(FakeIMAP):
        def uid(self, cmd, *args):
            if cmd == "STORE" and args[1] == "+X-GM-LABELS":
                raise m.imaplib.IMAP4.error("BAD Label name is not allowed")
            return super().uid(cmd, *args)

    runner = Runner()
    relay, _ = make_relay({}, tmp_path, runner, gmail_label="whatever")
    imap = LabelRefusingIMAP({b"1": raw_mail("[집-4821] 늦어요")})
    relay.imap_factory = imap
    assert relay.poll_once(relay.connect()) == 1
    assert len(runner.calls) == 1
    assert imap.flags[b"1"] == {"\\Seen"}
