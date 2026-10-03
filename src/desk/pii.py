"""B02: transport identities (WhatsApp wa_id, Telegram chat id) encrypted at rest.

Behind DESK_ENCRYPT_SENDERS (default OFF). OFF = values are stored exactly as before.
ON = every covered column is stored as "enc1:" + urlsafe-base64(AES-SIV ciphertext).

AES-SIV is deterministic: the same id always gives the same ciphertext, so equality
lookups, joins and unique constraints keep working on the encrypted column. Known limit:
anyone with DB access can still see that two rows hold the SAME id (not which id). One
fixed associated-data label is used for every column so cross-table equality works.

The key (DESK_SENDER_KEY, urlsafe base64 of 64 random bytes) lives in an env var until
the G05 key store exists. Key rotation (B06): `python -m desk senders rotate` re-encrypts
every covered value from DESK_SENDER_KEY to DESK_SENDER_KEY_NEW in one transaction.
"""

import base64
import binascii

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESSIV
from sqlalchemy import String, text
from sqlalchemy.types import TypeDecorator

from desk.logsafe import register_secret

PREFIX = "enc1:"
AAD = b"desk:transport-id:v1"
KEY_BYTES = 64  # AES-SIV-512 (two 256-bit halves)

# Every column that holds a transport id, or a key/id that embeds one:
#   tenants.sender, invites.bound_sender, inbound_messages.sender, outbox.recipient
#   inbound_messages.message_id / feedback.message_id / outbox.in_reply_to: Telegram
#     dedupe key "tg:<bot>:<chat id>:<msg>"; WhatsApp wamids encode the wa_id
#   outbox.idempotency_key: "<inbound message id>:<kind>", "neutral:...:<sender>:<day>"
#   outbox.provider_message_id / delivery_receipts.provider_message_id: Telegram
#     "tg:<chat id>:<msg>"; outbound WhatsApp wamids encode the recipient
COVERED: tuple[tuple[str, str], ...] = (
    ("tenants", "sender"),
    ("invites", "bound_sender"),
    ("inbound_messages", "sender"),
    ("inbound_messages", "message_id"),
    ("outbox", "recipient"),
    ("outbox", "idempotency_key"),
    ("outbox", "in_reply_to"),
    ("outbox", "provider_message_id"),
    ("delivery_receipts", "provider_message_id"),
    ("feedback", "message_id"),
)


class SenderKeyError(RuntimeError):
    """Encryption is misconfigured (flag ON without a valid key, or a stored value cannot
    be decrypted). Messages never contain the key or a stored value."""


class SenderStateError(RuntimeError):
    """Stored rows do not match the flag (mixed plaintext / encrypted)."""


def parse_key(raw: str, name: str = "DESK_SENDER_KEY") -> bytes:
    """urlsafe base64 (padding optional) of exactly 64 bytes, else SenderKeyError."""
    raw = (raw or "").strip()
    if not raw:
        raise SenderKeyError(f"DESK_ENCRYPT_SENDERS=1 needs {name} (not set)")
    try:
        key = base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        raise SenderKeyError(f"{name} is not urlsafe base64") from None
    if len(key) != KEY_BYTES:
        raise SenderKeyError(f"{name} must decode to exactly {KEY_BYTES} bytes")
    return key


def register_key(key: bytes) -> None:
    """Every text form of a key is redacted from logs (logsafe)."""
    for form in (base64.urlsafe_b64encode(key).decode(), base64.b64encode(key).decode()):
        register_secret(form)
        register_secret(form.rstrip("="))


class _State:
    enabled: bool = False
    cipher: AESSIV | None = None


_state = _State()


def configure(enabled: bool, key: bytes | None = None) -> None:
    """Set once at startup (app, CLI, runner). OFF ignores the key entirely."""
    if not enabled:
        _state.enabled, _state.cipher = False, None
        return
    if key is None or len(key) != KEY_BYTES:
        raise SenderKeyError(f"DESK_SENDER_KEY must decode to exactly {KEY_BYTES} bytes")
    register_key(key)
    _state.cipher, _state.enabled = AESSIV(key), True


def configure_from(settings) -> None:
    s = getattr(settings, "senders", None)
    configure(bool(s and s.enabled), s.key if s else None)


def enabled() -> bool:
    return _state.enabled


def encrypt(value: str) -> str:
    if _state.cipher is None:
        raise SenderKeyError("transport id encryption is not configured")
    return _seal(_state.cipher, value)


def _seal(cipher: AESSIV, value: str) -> str:
    ct = cipher.encrypt(value.encode(), [AAD])
    return PREFIX + base64.urlsafe_b64encode(ct).decode().rstrip("=")


def _open(cipher: AESSIV, stored: str) -> str | None:
    """Plaintext, or None when this key cannot decrypt the stored value."""
    body = stored[len(PREFIX) :]
    try:
        ct = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        return cipher.decrypt(ct, [AAD]).decode()
    except (InvalidTag, binascii.Error, ValueError):
        return None


def decrypt(stored: str) -> str:
    if _state.cipher is None:
        raise SenderKeyError(
            "a stored transport id is encrypted but no key is configured "
            "(set DESK_ENCRYPT_SENDERS=1 and DESK_SENDER_KEY)"
        )
    value = _open(_state.cipher, stored)
    if value is None:
        raise SenderKeyError(
            "a stored transport id could not be decrypted (wrong DESK_SENDER_KEY?)"
        )
    return value


def is_encrypted(value: str | None) -> bool:
    return value is not None and value.startswith(PREFIX)


class TransportId(TypeDecorator):
    """String column that encrypts on write when the flag is ON and always decrypts
    "enc1:" values on read. A ciphertext is never handed back as an id."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or not _state.enabled:
            return value
        return encrypt(value)

    def process_result_value(self, value, dialect):
        if value is None or not value.startswith(PREFIX):
            return value
        return decrypt(value)


# ---- stored-state checks and the one-off migration (operator CLI) -------------------


def storage_state(session) -> dict[tuple[str, str], tuple[int, int]]:
    """(table, column) -> (plaintext rows, encrypted rows). Raw SQL, no decryption."""
    out = {}
    for table, col in COVERED:
        plain, enc = session.execute(
            text(
                f"SELECT count(*) FILTER (WHERE {col} NOT LIKE 'enc1:%'), "  # noqa: S608
                f"count(*) FILTER (WHERE {col} LIKE 'enc1:%') FROM {table}"
            )
        ).one()
        out[(table, col)] = (plain, enc)
    return out


def unreadable_columns(session, key: bytes | None = None) -> list[str]:
    """Covered columns whose sampled "enc1:" values do NOT open with the configured key
    (or `key`). Samples the lowest and highest stored value per column (cheap, index-
    friendly) so a rotation without the env swap is caught. Raw SQL; never returns or
    prints a key or a value. Empty when encryption is off and no key is given."""
    if key is not None:
        cipher = AESSIV(key)
    elif _state.cipher is not None:
        cipher = _state.cipher
    else:
        return []
    bad = []
    for table, col in COVERED:
        samples = session.execute(
            text(
                f"(SELECT {col} FROM {table} WHERE {col} LIKE 'enc1:%' "  # noqa: S608
                f"ORDER BY {col} LIMIT 1) UNION "
                f"(SELECT {col} FROM {table} WHERE {col} LIKE 'enc1:%' "
                f"ORDER BY {col} DESC LIMIT 1)"
            )
        ).scalars()
        if any(_open(cipher, v) is None for v in samples):
            bad.append(f"{table}.{col}")
    return bad


def check_startup(session) -> None:
    """Refuse to run when stored rows disagree with the flag: ON with plaintext rows left
    (lookups would miss them) or OFF with encrypted rows (they could not be read), or ON
    with stored ids that do not open with DESK_SENDER_KEY (e.g. `senders rotate` ran but
    the env var still holds the OLD key: every read would fail and new rows would be
    written under the old key)."""
    want_plain = not _state.enabled
    bad = []
    for table, col in COVERED:
        cond = f"{col} LIKE 'enc1:%'" if want_plain else f"{col} NOT LIKE 'enc1:%'"
        if session.execute(
            text(f"SELECT EXISTS (SELECT 1 FROM {table} WHERE {cond})")  # noqa: S608
        ).scalar():
            bad.append(f"{table}.{col}")
    if not bad and not want_plain:
        unreadable = unreadable_columns(session)
        if unreadable:
            raise SenderStateError(
                "stored transport ids do not open with DESK_SENDER_KEY in "
                + ", ".join(unreadable)
                + "; after `senders rotate` set DESK_SENDER_KEY to the new key"
            )
    if not bad:
        return
    if want_plain:
        raise SenderStateError(
            "DESK_ENCRYPT_SENDERS is off but encrypted transport ids are stored in "
            + ", ".join(bad)
            + "; turn the flag back on with the same key"
        )
    raise SenderStateError(
        "DESK_ENCRYPT_SENDERS is on but plaintext transport ids remain in "
        + ", ".join(bad)
        + "; run `python -m desk senders encrypt` first"
    )


def encrypt_existing(session) -> dict[tuple[str, str], int]:
    """Rewrite every plaintext value in COVERED to its encrypted form. Caller commits
    (one transaction). Idempotent: encrypted values are skipped."""
    if not _state.enabled:
        raise SenderKeyError("senders encrypt needs DESK_ENCRYPT_SENDERS=1 and DESK_SENDER_KEY")
    changed = {}
    for table, col in COVERED:
        values = session.execute(
            text(
                f"SELECT DISTINCT {col} FROM {table} "  # noqa: S608
                f"WHERE {col} IS NOT NULL AND {col} NOT LIKE 'enc1:%'"
            )
        ).scalars()
        n = 0
        for value in list(values):
            n += session.execute(
                text(f"UPDATE {table} SET {col} = :new WHERE {col} = :old"),  # noqa: S608
                {"new": encrypt(value), "old": value},
            ).rowcount
        changed[(table, col)] = n
    return changed


def rotate_existing(session, old_key: bytes, new_key: bytes) -> tuple[int, int]:
    """B06: re-encrypt every covered value from old_key to new_key. Caller commits (one
    transaction) or rolls back. Returns (rows rotated, distinct values already under the
    new key). Safe to re-run: each value is tried with the NEW key first and skipped if it
    opens. A value neither key opens raises SenderKeyError (no key, no value in the text);
    plaintext values left raise SenderStateError. Either way the caller rolls back.

    Stop `serve` before rotating. As a guard, every covered table is locked IN EXCLUSIVE
    MODE first (reads still work): a writer that is still running waits for the commit
    instead of adding rows under the old key mid-rotation; it must then be restarted
    with the new key (startup refuses the old one)."""
    if old_key == new_key:
        raise SenderKeyError("DESK_SENDER_KEY_NEW must differ from DESK_SENDER_KEY")
    tables = ", ".join(dict.fromkeys(t for t, _ in COVERED))
    session.execute(text(f"LOCK TABLE {tables} IN EXCLUSIVE MODE"))
    old, new = AESSIV(old_key), AESSIV(new_key)
    plain = [f"{t}.{c}" for (t, c), (p, _) in storage_state(session).items() if p]
    if plain:
        raise SenderStateError(
            "plaintext transport ids remain in "
            + ", ".join(plain)
            + "; run `python -m desk senders encrypt` first"
        )
    rotated = skipped = 0
    for table, col in COVERED:
        values = session.execute(
            text(
                f"SELECT DISTINCT {col} FROM {table} "  # noqa: S608
                f"WHERE {col} LIKE 'enc1:%'"
            )
        ).scalars()
        for stored in list(values):
            if _open(new, stored) is not None:
                skipped += 1
                continue
            value = _open(old, stored)
            if value is None:
                raise SenderKeyError(
                    f"a stored transport id in {table}.{col} opens with neither "
                    "DESK_SENDER_KEY nor DESK_SENDER_KEY_NEW; nothing was changed"
                )
            rotated += session.execute(
                text(f"UPDATE {table} SET {col} = :new WHERE {col} = :old"),  # noqa: S608
                {"new": _seal(new, value), "old": stored},
            ).rowcount
    return rotated, skipped
