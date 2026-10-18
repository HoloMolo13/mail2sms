"""Gmail -> SMS relay for Termux.

Polls a Gmail inbox over IMAP, and for every unread mail that comes from an
allowed sender and whose subject starts with "[<prefix>-<secret code>]",
sends the rest of the subject (or the first body line) as an SMS to one fixed
number with `termux-sms-send`.

Standard library only (Python 3.11+ for tomllib).
"""

from __future__ import annotations

import argparse
import datetime as dt
import email
import email.policy
import hmac
import html
import imaplib
import json
import logging
import logging.handlers
import os
import re
import smtplib
import stat
import subprocess
import sys
import time
import tomllib
import unicodedata
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import getaddresses, make_msgid
from pathlib import Path
from typing import Callable

log = logging.getLogger("mail2sms")

PASSWORD_ENV = "MAIL2SMS_APP_PASSWORD"
PLACEHOLDERS = {"CHANGE_ME", "you@gmail.com", "me@company.com", "01000000000"}
PHONE_RE = re.compile(r"^\+?[0-9]{6,15}$")


class ConfigError(Exception):
    pass


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


@dataclass
class Config:
    imap_user: str
    allowed_senders: frozenset[str]
    subject_prefix: str
    secret_code: str
    recipient: str
    dry_run: bool = True
    poll_interval_sec: int = 60
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    mailbox: str = "INBOX"
    lookback_days: int = 2
    password_file: Path | None = None
    require_dmarc_pass: bool = False
    max_chars: int = 70
    max_per_hour: int = 5
    min_interval_sec: int = 60
    sms_timeout_sec: int = 60
    gmail_label: str = ""
    reply_enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    log_file: Path | None = None
    state_file: Path | None = None

    @property
    def tag(self) -> str:
        return f"[{self.subject_prefix}-{self.secret_code}]"

    @property
    def tag_start(self) -> str:
        return f"[{self.subject_prefix}-"


def _path(value: str | None, base: Path) -> Path | None:
    if not value:
        return None
    p = Path(os.path.expanduser(value))
    return p if p.is_absolute() else (base / p)


def load_config(path: str | Path) -> Config:
    path = Path(path)
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    base = path.parent

    imap = raw.get("imap", {})
    flt = raw.get("filter", {})
    sms = raw.get("sms", {})
    gmail = raw.get("gmail", {})
    reply = raw.get("reply", {})
    logs = raw.get("log", {})

    try:
        cfg = Config(
            imap_user=str(imap["user"]).strip(),
            allowed_senders=frozenset(
                str(s).strip().lower() for s in flt["allowed_senders"]
            ),
            subject_prefix=_nfc(str(flt["subject_prefix"]).strip()),
            secret_code=_nfc(str(flt["secret_code"]).strip()),
            recipient=str(sms["recipient"]).strip().replace("-", ""),
            dry_run=bool(raw.get("dry_run", True)),
            poll_interval_sec=int(raw.get("poll_interval_sec", 60)),
            imap_host=imap.get("host", "imap.gmail.com"),
            imap_port=int(imap.get("port", 993)),
            mailbox=imap.get("mailbox", "INBOX"),
            lookback_days=int(imap.get("lookback_days", 2)),
            password_file=_path(imap.get("password_file"), base),
            require_dmarc_pass=bool(flt.get("require_dmarc_pass", False)),
            max_chars=int(sms.get("max_chars", 70)),
            max_per_hour=int(sms.get("max_per_hour", 5)),
            min_interval_sec=int(sms.get("min_interval_sec", 60)),
            sms_timeout_sec=int(sms.get("timeout_sec", 60)),
            gmail_label=str(gmail.get("label", "")).strip(),
            reply_enabled=bool(reply.get("enabled", False)),
            smtp_host=reply.get("smtp_host", "smtp.gmail.com"),
            smtp_port=int(reply.get("smtp_port", 465)),
            log_file=_path(logs.get("file"), base),
            state_file=_path(logs.get("state_file"), base),
        )
    except KeyError as e:
        raise ConfigError(f"missing config key: {e.args[0]}") from None

    validate_config(cfg)
    return cfg


def validate_config(cfg: Config) -> None:
    problems = []
    if not cfg.imap_user or cfg.imap_user in PLACEHOLDERS:
        problems.append("imap.user 를 본인 Gmail 주소로 바꾸세요")
    if not cfg.allowed_senders or cfg.allowed_senders & PLACEHOLDERS:
        problems.append("filter.allowed_senders 를 실제 보낼 주소로 바꾸세요")
    if not cfg.subject_prefix or "]" in cfg.subject_prefix or "-" in cfg.subject_prefix:
        problems.append("filter.subject_prefix 는 비어 있지 않고 ']' '-' 가 없어야 합니다")
    if cfg.secret_code in PLACEHOLDERS or len(cfg.secret_code) < 4 or "]" in cfg.secret_code:
        problems.append("filter.secret_code 를 4자 이상(']' 제외)의 비밀 코드로 바꾸세요")
    if cfg.recipient in PLACEHOLDERS or not PHONE_RE.match(cfg.recipient):
        problems.append("sms.recipient 를 받는 사람 번호(숫자만)로 바꾸세요")
    if cfg.max_chars < 1:
        problems.append("sms.max_chars 는 1 이상이어야 합니다")
    if cfg.max_per_hour < 1:
        problems.append("sms.max_per_hour 는 1 이상이어야 합니다")
    if cfg.poll_interval_sec < 10:
        problems.append("poll_interval_sec 는 10 이상이어야 합니다")
    if cfg.gmail_label and not cfg.gmail_label.isascii():
        problems.append("gmail.label 은 영문/숫자로 지정하세요")
    if problems:
        raise ConfigError("; ".join(problems))


def load_password(cfg: Config) -> str:
    """App password from $MAIL2SMS_APP_PASSWORD, else from a mode-600 file."""
    pw = os.environ.get(PASSWORD_ENV)
    if not pw:
        if cfg.password_file is None:
            raise ConfigError(
                f"앱 비밀번호가 없습니다: 환경변수 {PASSWORD_ENV} 또는 imap.password_file"
            )
        p = cfg.password_file
        if not p.is_file():
            raise ConfigError(f"비밀번호 파일이 없습니다: {p}")
        if os.name == "posix":
            mode = stat.S_IMODE(p.stat().st_mode)
            if mode & 0o077:
                raise ConfigError(
                    f"비밀번호 파일 권한이 너무 넓습니다({oct(mode)}): chmod 600 {p}"
                )
        pw = p.read_text(encoding="utf-8")
    # Google shows app passwords as "abcd efgh ijkl mnop".
    pw = "".join(pw.split())
    if not pw:
        raise ConfigError("앱 비밀번호가 비어 있습니다")
    return pw


# --------------------------------------------------------------------------
# Parsing and filtering
# --------------------------------------------------------------------------


@dataclass
class ParsedMail:
    sender: str
    subject: str
    first_line: str
    message_id: str = ""
    authentication_results: str = ""
    from_count: int = 1


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")


def _clean(s: str) -> str:
    return " ".join(_CONTROL_RE.sub(" ", _nfc(s)).split())


def _first_text_line(msg: email.message.EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        text = part.get_content()
    except (LookupError, ValueError):
        payload = part.get_payload(decode=True) or b""
        text = payload.decode("utf-8", errors="replace")
    if part.get_content_type() == "text/html":
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
        text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
    for line in text.splitlines():
        line = _clean(line)
        if line:
            return line
    return ""


def parse_message(raw: bytes) -> ParsedMail:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    froms = msg.get_all("From", [])
    addrs = [a for _, a in getaddresses([str(f) for f in froms]) if a]
    return ParsedMail(
        sender=addrs[0].strip().lower() if addrs else "",
        subject=_clean(str(msg.get("Subject", ""))),
        first_line=_first_text_line(msg),
        message_id=str(msg.get("Message-ID", "")).strip(),
        authentication_results=" ".join(
            str(h) for h in msg.get_all("Authentication-Results", [])
        ),
        from_count=len(addrs),
    )


# Outcomes of evaluate():
IGNORE = "ignore"  # not addressed to us: leave untouched (stays unread)
REJECT = "reject"  # looks like a relay request but fails a check
ACCEPT = "accept"


@dataclass
class Decision:
    action: str
    reason: str = ""
    text: str = ""


def _dmarc_pass(auth_results: str) -> bool:
    return re.search(r"\bdmarc=pass\b", auth_results, re.IGNORECASE) is not None


def evaluate(mail: ParsedMail, cfg: Config) -> Decision:
    if mail.from_count != 1 or mail.sender not in cfg.allowed_senders:
        return Decision(IGNORE, "sender not allowed")

    subject = mail.subject
    if not subject.startswith(cfg.tag_start):
        return Decision(IGNORE, "no tag")

    head = subject[: len(cfg.tag)]
    if not hmac.compare_digest(head.encode("utf-8"), cfg.tag.encode("utf-8")):
        return Decision(REJECT, "wrong secret code")

    if cfg.require_dmarc_pass and not _dmarc_pass(mail.authentication_results):
        return Decision(REJECT, "DMARC not passed")

    text = subject[len(cfg.tag):].strip() or mail.first_line
    text = make_sms_text(text, cfg.max_chars)
    if not text:
        return Decision(REJECT, "empty message")
    return Decision(ACCEPT, "ok", text)


def make_sms_text(text: str, max_chars: int) -> str:
    return _clean(text)[:max_chars].rstrip()


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------


class RateLimiter:
    """At most `max_per_hour` sends in any 3600 s window, and at least
    `min_interval_sec` between sends. Persisted so restarts don't reset it."""

    def __init__(self, max_per_hour: int, min_interval_sec: int, state_file: Path | None = None):
        self.max_per_hour = max_per_hour
        self.min_interval_sec = min_interval_sec
        self.state_file = state_file
        self.sent: list[float] = []
        self._load()

    def _load(self) -> None:
        if self.state_file and self.state_file.is_file():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                self.sent = sorted(float(t) for t in data.get("sent", []))
            except (ValueError, TypeError, OSError) as e:
                log.warning("state file unreadable, starting empty: %s", e)

    def _save(self) -> None:
        if not self.state_file:
            return
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"sent": self.sent}), encoding="utf-8")
        os.replace(tmp, self.state_file)

    def check(self, now: float) -> tuple[bool, str]:
        self.sent = [t for t in self.sent if now - t < 3600]
        if self.sent and now - self.sent[-1] < self.min_interval_sec:
            wait = int(self.min_interval_sec - (now - self.sent[-1])) + 1
            return False, f"min interval ({wait}s left)"
        if len(self.sent) >= self.max_per_hour:
            return False, f"hourly limit {self.max_per_hour} reached"
        return True, ""

    def record(self, now: float) -> None:
        self.sent.append(now)
        self._save()


# --------------------------------------------------------------------------
# SMS
# --------------------------------------------------------------------------


def build_sms_command(recipient: str, text: str) -> list[str]:
    if not PHONE_RE.match(recipient):
        raise ValueError("invalid recipient")
    # termux-sms-send parses options with getopts and then `echo`s the text,
    # so a message starting with "-" (e.g. "-n") would be eaten as an option.
    if text.startswith("-"):
        text = " " + text
    return ["termux-sms-send", "-n", recipient, text]


Runner = Callable[..., subprocess.CompletedProcess]


def send_sms(cfg: Config, text: str, runner: Runner = subprocess.run) -> tuple[bool, str]:
    cmd = build_sms_command(cfg.recipient, text)
    if cfg.dry_run:
        log.info("[dry-run] would run: %s", cmd[:3] + ["<text>"])
        return True, "dry-run"
    try:
        r = runner(cmd, capture_output=True, text=True, timeout=cfg.sms_timeout_sec)
    except FileNotFoundError:
        return False, "termux-sms-send not found (pkg install termux-api)"
    except subprocess.TimeoutExpired:
        return False, "termux-sms-send timed out"
    if r.returncode != 0:
        return False, f"exit {r.returncode}: {(r.stderr or '').strip()[:200]}"
    return True, "sent"


# --------------------------------------------------------------------------
# Reply mail
# --------------------------------------------------------------------------


def build_reply(cfg: Config, mail: ParsedMail, status: str, detail: str) -> EmailMessage:
    m = EmailMessage()
    m["From"] = cfg.imap_user
    m["To"] = mail.sender
    # Never echo the original subject: it contains the secret code.
    m["Subject"] = f"[mail2sms] {status}"
    m["Message-ID"] = make_msgid()
    if mail.message_id:
        m["In-Reply-To"] = mail.message_id
        m["References"] = mail.message_id
    m.set_content(f"{status}\n{detail}\n")
    return m


def send_reply(cfg: Config, password: str, msg: EmailMessage, smtp_factory=smtplib.SMTP_SSL) -> None:
    with smtp_factory(cfg.smtp_host, cfg.smtp_port, timeout=30) as s:
        s.login(cfg.imap_user, password)
        s.send_message(msg)


# --------------------------------------------------------------------------
# IMAP processing
# --------------------------------------------------------------------------


def _imap_date(d: dt.date) -> str:
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{d.day:02d}-{months[d.month - 1]}-{d.year}"


def _fetch_bytes(data) -> bytes | None:
    for item in data or []:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return None


@dataclass
class Relay:
    cfg: Config
    password: str
    limiter: RateLimiter
    imap_factory: Callable = imaplib.IMAP4_SSL
    runner: Runner = subprocess.run
    smtp_factory: Callable = smtplib.SMTP_SSL
    clock: Callable[[], float] = time.time
    # UIDs already looked at and left alone (not ours), keyed by UIDVALIDITY.
    ignored: set[bytes] = field(default_factory=set)
    uidvalidity: bytes = b""

    def connect(self):
        conn = self.imap_factory(self.cfg.imap_host, self.cfg.imap_port)
        conn.login(self.cfg.imap_user, self.password)
        typ, _ = conn.select(self.cfg.mailbox)
        if typ != "OK":
            raise imaplib.IMAP4.error(f"cannot select {self.cfg.mailbox}")
        try:
            uv = conn.response("UIDVALIDITY")[1]
            uv = uv[0] if uv and uv[0] else b""
        except Exception:
            uv = b""
        if uv != self.uidvalidity:
            self.ignored.clear()
            self.uidvalidity = uv
        return conn

    def poll_once(self, conn) -> int:
        """Process unread mail once. Returns the number of handled mails."""
        since = dt.date.fromtimestamp(self.clock()) - dt.timedelta(days=self.cfg.lookback_days)
        typ, data = conn.uid("SEARCH", None, "UNSEEN", "SINCE", _imap_date(since))
        if typ != "OK":
            raise imaplib.IMAP4.error(f"search failed: {data}")
        uids = data[0].split() if data and data[0] else []
        handled = 0
        for uid in uids:
            if uid in self.ignored:
                continue
            # BODY.PEEK keeps the \Seen flag untouched for mail that isn't ours.
            typ, fd = conn.uid("FETCH", uid, "(BODY.PEEK[])")
            raw = _fetch_bytes(fd) if typ == "OK" else None
            if raw is None:
                log.warning("uid %s: fetch failed", uid)
                continue
            if self.handle(conn, uid, raw):
                handled += 1
        return handled

    def handle(self, conn, uid: bytes, raw: bytes) -> bool:
        mail = parse_message(raw)
        decision = evaluate(mail, self.cfg)
        if decision.action == IGNORE:
            self.ignored.add(uid)
            return False

        if decision.action == ACCEPT:
            now = self.clock()
            ok, why = self.limiter.check(now)
            if not ok:
                decision = Decision(REJECT, f"rate limited: {why}")

        if decision.action == REJECT:
            log.warning("uid %s from %s rejected: %s", uid.decode(), mail.sender, decision.reason)
            status, detail = "거부됨", decision.reason
        else:
            ok, detail = send_sms(self.cfg, decision.text, self.runner)
            if ok:
                self.limiter.record(self.clock())
                status = "보냈음" + (" (테스트 모드, 실제 전송 안 함)" if self.cfg.dry_run else "")
                log.info("uid %s from %s: %s (%d chars)", uid.decode(), mail.sender, detail, len(decision.text))
                detail = f"문구: {decision.text}"
            else:
                status = "전송 실패"
                log.error("uid %s from %s: sms failed: %s", uid.decode(), mail.sender, detail)

        self.mark_done(conn, uid)
        if self.cfg.reply_enabled:
            try:
                send_reply(self.cfg, self.password, build_reply(self.cfg, mail, status, detail), self.smtp_factory)
            except (smtplib.SMTPException, OSError) as e:
                log.error("reply mail failed: %s", e)
        return True

    def mark_done(self, conn, uid: bytes) -> None:
        conn.uid("STORE", uid, "+FLAGS", "(\\Seen)")
        if self.cfg.gmail_label:
            conn.uid("STORE", uid, "+X-GM-LABELS", f'("{self.cfg.gmail_label}")')

    def run_forever(self, sleep: Callable[[float], None] = time.sleep) -> None:
        backoff = self.cfg.poll_interval_sec
        conn = None
        while True:
            try:
                if conn is None:
                    conn = self.connect()
                    log.info("connected to %s as %s", self.cfg.imap_host, self.cfg.imap_user)
                conn.noop()
                self.poll_once(conn)
                backoff = self.cfg.poll_interval_sec
            except (imaplib.IMAP4.error, OSError) as e:
                log.error("imap error: %s", e)
                _close(conn)
                conn = None
                backoff = min(backoff * 2, 900)
            sleep(backoff)


def _close(conn) -> None:
    if conn is None:
        return
    try:
        conn.logout()
    except Exception:
        pass


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def setup_logging(log_file: Path | None) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Gmail -> SMS relay for Termux")
    ap.add_argument("--config", default=os.path.expanduser("~/.config/mail2sms/config.toml"))
    ap.add_argument("--once", action="store_true", help="poll once and exit")
    ap.add_argument("--check", action="store_true", help="validate config and password, then exit")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config)
        password = load_password(cfg)
    except (ConfigError, OSError, tomllib.TOMLDecodeError) as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        return 2

    setup_logging(cfg.log_file)
    if args.check:
        log.info("config OK (dry_run=%s, tag prefix=%r)", cfg.dry_run, cfg.subject_prefix)
        return 0

    limiter = RateLimiter(cfg.max_per_hour, cfg.min_interval_sec, cfg.state_file)
    relay = Relay(cfg, password, limiter)
    log.info("mail2sms starting (dry_run=%s, every %ss)", cfg.dry_run, cfg.poll_interval_sec)
    if args.once:
        conn = relay.connect()
        try:
            n = relay.poll_once(conn)
        finally:
            _close(conn)
        log.info("handled %d mail(s)", n)
        return 0
    try:
        relay.run_forever()
    except KeyboardInterrupt:
        log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
