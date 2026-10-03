"""Log redaction (RC08). Telegram puts the bot token inside every request URL, and
HTTP libraries log request URLs and put them in exception text, so every channel that
can carry text to a log is scrubbed (HIGH-3):

1. each record at creation (Logger.makeRecord): the message, a pre-rendered and redacted
   traceback (exc_info is then dropped so no formatter re-renders the raw one, and the
   frames' local variables are released), stack_info, and every `extra=` field;
2. the final formatted line of every logging.Formatter (records built without a Logger);
3. uncaught exceptions in the main thread and in threads (sys/threading excepthook).

B02/B06: the DESK_SENDER_KEY (and DESK_SENDER_KEY_NEW) value is redacted by shape, by
`DESK_SENDER_KEY=` / `DESK_SENDER_KEY_NEW=` and, once configured, by its exact text.

The HTTP libraries' own request logging is also turned down to warnings."""

import logging
import re
import sys
import threading
import traceback

TOKEN_PATTERNS = [
    re.compile(r"bot\d{5,}:[A-Za-z0-9_-]{20,}"),  # inside api.telegram.org URLs
    re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{30,}\b"),  # bare Telegram bot token
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{16,}"),  # Graph API access token header
    re.compile(r"DESK_SENDER_KEY(?:_NEW)?\s*[=:]\s*['\"]?[^\s'\",;]+"),  # B02 key in an env dump
    # B02 key shape: urlsafe base64 of 64 bytes (86 chars, optional "==" padding)
    re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{86}(?:==)?(?![A-Za-z0-9_=-])"),
]
_exact: list[re.Pattern[str]] = []  # configured secret values (B02 key), any shape
REDACTED = "[REDACTED]"
_STANDARD = set(vars(logging.LogRecord("x", 0, "x", 0, "x", None, None))) | {"message"}
_installed = False


def register_secret(value: str) -> None:
    """Redact this exact value too (called at startup with the B02 key's text forms)."""
    if value and not any(p.pattern == re.escape(value) for p in _exact):
        _exact.append(re.compile(re.escape(value)))


def redact(text: str) -> str:
    for p in _exact:
        text = p.sub(REDACTED, text)
    for p in TOKEN_PATTERNS:
        text = p.sub(REDACTED, text)
    return text


def scrub(record: logging.LogRecord) -> logging.LogRecord:
    """Redact everything a handler or formatter could print from this record."""
    try:
        message = record.getMessage()
    except Exception:  # noqa: BLE001 - never let logging crash the desk
        message = f"{record.msg!s} (unformattable args)"
    record.msg, record.args = redact(message), None
    if record.exc_info and record.exc_info[0] is not None:
        text = "".join(traceback.format_exception(*record.exc_info)).rstrip("\n")
        record.exc_text, record.exc_info = redact(text), None
    elif record.exc_text:
        record.exc_text = redact(record.exc_text)
    if record.stack_info:
        record.stack_info = redact(record.stack_info)
    for key, value in list(vars(record).items()):
        if key in _STANDARD:
            continue
        try:
            text = value if isinstance(value, str) else repr(value)
        except Exception:  # noqa: BLE001 - an unprintable value is dropped, not kept raw
            text = REDACTED
        if redact(text) != text or text == REDACTED:  # only touch fields that carried one
            setattr(record, key, redact(text))
    return record


def _excepthook(exc_type, exc, tb) -> None:
    sys.stderr.write(redact("".join(traceback.format_exception(exc_type, exc, tb))))


def _thread_excepthook(args) -> None:
    if args.exc_type is SystemExit:
        return
    name = args.thread.name if args.thread else "?"
    text = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    sys.stderr.write(redact(f"Exception in thread {name}:\n{text}"))


def install() -> None:
    global _installed
    if _installed:
        return
    make_record = logging.Logger.makeRecord

    def safe_make_record(self, *args, **kwargs):  # runs after `extra` is applied
        return scrub(make_record(self, *args, **kwargs))

    fmt = logging.Formatter.format

    def safe_format(self, record):
        return redact(fmt(self, record))

    logging.Logger.makeRecord = safe_make_record  # type: ignore[method-assign]
    logging.Formatter.format = safe_format  # type: ignore[method-assign]
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    _installed = True
