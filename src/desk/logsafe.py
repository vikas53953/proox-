"""Log redaction (RC08). Telegram puts the bot token inside every request URL, and
HTTP libraries log request URLs, so every log record is scrubbed at creation time and
the HTTP libraries' own request logging is turned down to warnings."""

import logging
import re

TOKEN_PATTERNS = [
    re.compile(r"bot\d{5,}:[A-Za-z0-9_-]{20,}"),  # inside api.telegram.org URLs
    re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{30,}\b"),  # bare Telegram bot token
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{16,}"),  # Graph API access token header
]
REDACTED = "[REDACTED]"
_installed = False


def redact(text: str) -> str:
    for p in TOKEN_PATTERNS:
        text = p.sub(REDACTED, text)
    return text


def install() -> None:
    global _installed
    if _installed:
        return
    old_factory = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never let logging crash the desk
            return record
        record.msg, record.args = redact(message), None
        return record

    logging.setLogRecordFactory(factory)
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    _installed = True
