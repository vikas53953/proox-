"""Minimal Telegram Bot API client over the pinned HTTPX (no Telegram SDK — BOM).

Only long-stable methods are used: getMe, getUpdates, sendMessage, sendDocument,
sendPhoto. Plain text only (no parse_mode), so report text can't be turned into markup.
"""

import json
import re
import uuid

import httpx

from desk.transport.base import TELEGRAM, Outcome, SendResult

API_BASE = "https://api.telegram.org"
# Placeholder for the in-memory fake only; real tokens come from the environment.
MOCK_TOKEN = "0:MOCK"  # noqa: S105 - not token-shaped on purpose (E01 scan stays strict)
TIMEOUT = httpx.Timeout(35.0, connect=5.0)  # long-poll waits up to 25 s


class TelegramPollError(RuntimeError):
    """getUpdates/getMe failed. Messages never contain the request URL (it holds the token)."""


class TelegramTransientError(TelegramPollError):
    """Network trouble, 5xx, 429 or an unreadable answer: back off and try again.
    `retry_after_s` carries Telegram's own wait (429 `parameters.retry_after`) when given."""

    def __init__(self, message: str, retry_after_s: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class TelegramFatalError(TelegramPollError):
    """Retrying cannot help: 401/404 (token rejected) or 409 (another poller or a webhook
    is using this bot). The serve loop stops and tells the owner."""


def _api_error(method: str, r: httpx.Response) -> TelegramPollError:
    try:
        body = r.json()
    except json.JSONDecodeError:
        body = {}
    code = int(body.get("error_code", r.status_code)) if isinstance(body, dict) else r.status_code
    desc = str(body.get("description", "") if isinstance(body, dict) else "")[:100]
    if code in (401, 404):
        return TelegramFatalError(f"{method}: {code} token rejected; check TELEGRAM_BOT_TOKEN")
    if code == 409:
        return TelegramFatalError(
            f"{method}: 409 another getUpdates poller or a webhook is active for this bot"
        )
    retry = None
    if code == 429 and isinstance(body, dict):
        raw = (body.get("parameters") or {}).get("retry_after")
        retry = int(raw) if isinstance(raw, int | str) and str(raw).isdigit() else None
    return TelegramTransientError(f"{method}: {code} {desc}".rstrip(), retry_after_s=retry)


class TelegramClient:
    def __init__(self, http: httpx.Client, token: str) -> None:
        self._http = http
        self._base = f"{API_BASE}/bot{token}"  # contains the secret: never log or echo

    def _call(
        self, method: str, data: dict | None = None, files: dict | None = None
    ) -> httpx.Response:
        return self._http.post(f"{self._base}/{method}", data=data, files=files, timeout=TIMEOUT)

    def _read(self, method: str, data: dict | None = None):
        """Call a read method; raise a typed, token-free error on any failure (MED-5)."""
        try:
            r = self._call(method, data=data)
        except httpx.TransportError as exc:  # str(exc) is not used: keep URLs out
            raise TelegramTransientError(f"{method}: {type(exc).__name__}") from None
        try:
            body = r.json()
        except json.JSONDecodeError:
            body = None
        if r.status_code == 200 and isinstance(body, dict) and body.get("ok"):
            return body["result"]
        raise _api_error(method, r)

    def get_me(self) -> dict:
        return self._read("getMe")

    def get_updates(
        self, offset: int, timeout_s: int = 25, member_updates: bool = False
    ) -> list[dict]:
        """member_updates (TELEGRAM_TRACK_MEMBER_UPDATES) adds `my_chat_member`; off, the
        request is exactly what it always was."""
        allowed = ["message", "my_chat_member"] if member_updates else ["message"]
        return self._read(
            "getUpdates",
            {"offset": offset, "timeout": timeout_s, "allowed_updates": json.dumps(allowed)},
        )

    def send(self, method: str, data: dict, files: dict | None = None) -> Outcome:
        try:
            r = self._call(method, data=data, files=files)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return Outcome(SendResult.RETRY, detail="connection failed before sending")
        except httpx.TransportError as exc:
            return Outcome(
                SendResult.UNKNOWN, detail=f"no answer after sending: {type(exc).__name__}"
            )
        if r.status_code >= 500:
            return Outcome(SendResult.UNKNOWN, detail=f"HTTP {r.status_code}")
        try:
            body = r.json()
        except json.JSONDecodeError:
            return Outcome(SendResult.UNKNOWN, detail="unreadable response")
        if body.get("ok"):
            msg = body.get("result") or {}
            mid = f"tg:{msg.get('chat', {}).get('id')}:{msg.get('message_id')}"
            return Outcome(SendResult.SENT, provider_message_id=mid)
        code = int(body.get("error_code", r.status_code))
        desc = str(body.get("description", ""))[:200]
        if code == 429:
            retry = (body.get("parameters") or {}).get("retry_after")
            return Outcome(
                SendResult.RETRY, detail=f"429: {desc}", retry_after_s=int(retry) if retry else None
            )
        if code == 403:
            return Outcome(SendResult.BLOCKED, detail=f"403: {desc}")
        if code in (401, 404):  # our token, not the recipient: keep the message for later
            return Outcome(SendResult.RETRY, detail=f"{code}: bot token rejected")
        return Outcome(SendResult.FAILED, detail=f"{code}: {desc}")


class TelegramTransport:
    caps = TELEGRAM

    def __init__(
        self,
        client: TelegramClient,
        bot_id: str,
        allowed_recipients: frozenset[str] | None = None,
    ) -> None:
        self.client = client
        self.endpoint = str(bot_id)  # sends only rows of THIS bot (HIGH-1/HIGH-2)
        self.allowed_recipients = allowed_recipients  # B07; None = no restriction

    def send_text(self, to: str, body: str, ref: str) -> Outcome:
        return self.client.send(
            "sendMessage",
            {"chat_id": to, "text": body[: TELEGRAM.max_text], "disable_web_page_preview": "true"},
        )

    def send_media(
        self, to: str, kind: str, content: bytes, mime: str, filename: str, caption: str, ref: str
    ) -> Outcome:
        method, field = ("sendPhoto", "photo") if kind == "image" else ("sendDocument", "document")
        return self.client.send(
            method,
            {"chat_id": to, "caption": caption[: TELEGRAM.max_caption]},
            files={field: (filename, content, mime)},
        )

    def send_template(
        self, to: str, name: str, language: str, params: list[str], ref: str
    ) -> Outcome:
        return Outcome(SendResult.FAILED, detail="Telegram has no message templates")


class FakeTelegram:
    """In-memory api.telegram.org for tests and the container (Telegram is blocked here)."""

    def __init__(self, bot_id: int = 777000111, username: str = "desk_mock_bot") -> None:
        self.bot_id, self.username = bot_id, username
        self.calls: list[dict] = []
        self.updates: list[dict] = []
        self.script: list = []  # per send: "ok" | dict error body | int status | Exception
        self.poll_script: list = []  # per getUpdates, same items; then the real queue
        self.poll_requests: list[bytes] = []  # raw getUpdates bodies, as sent
        self._next_update = 1000
        self.confirmed_offset = 0

    def user_says(
        self,
        user_id: int,
        text: str,
        chat_type: str = "private",
        is_bot: bool = False,
        update_id: int | None = None,
        message_id: int | None = None,
    ) -> int:
        """Queue one incoming message. update_id/message_id can be forced to simulate
        Telegram re-randomising update ids or re-delivering the same message."""
        self._next_update = update_id if update_id is not None else self._next_update + 1
        self.updates.append(
            {
                "update_id": self._next_update,
                "message": {
                    "message_id": message_id if message_id is not None else self._next_update,
                    "date": 1790000000,
                    "text": text,
                    "chat": {"id": user_id, "type": chat_type},
                    "from": {"id": user_id, "is_bot": is_bot, "first_name": "MOCK"},
                },
            }
        )
        return self._next_update

    def member_update(
        self,
        user_id: int,
        new_status: str = "kicked",
        old_status: str = "member",
        chat_type: str = "private",
        date: int = 1790000100,
        update_id: int | None = None,
    ) -> int:
        """Queue a `my_chat_member` update ("kicked" = the person blocked the bot).
        Like real Telegram, it is only delivered when allowed_updates asks for it."""
        self._next_update = update_id if update_id is not None else self._next_update + 1
        bot = {"id": self.bot_id, "is_bot": True, "username": self.username}
        self.updates.append(
            {
                "update_id": self._next_update,
                "my_chat_member": {
                    "chat": {"id": user_id, "type": chat_type},
                    "from": {"id": user_id, "is_bot": False, "first_name": "MOCK"},
                    "date": date,
                    "old_chat_member": {"status": old_status, "user": bot},
                    "new_chat_member": {"status": new_status, "user": bot},
                },
            }
        )
        return self._next_update

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "getMe":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {"id": self.bot_id, "is_bot": True, "username": self.username},
                },
            )
        if method == "getUpdates":
            step = self.poll_script.pop(0) if self.poll_script else "ok"
            if isinstance(step, Exception):
                raise step
            if isinstance(step, int):
                return httpx.Response(step, text="error")
            if isinstance(step, dict):
                return httpx.Response(step.get("error_code", 400), json={"ok": False, **step})
            self.poll_requests.append(request.content)
            form = dict(httpx.QueryParams(request.content.decode()))
            offset = int(form.get("offset", 0))
            self.confirmed_offset = max(self.confirmed_offset, offset)
            self.updates = [u for u in self.updates if u["update_id"] >= offset]
            wanted = set(json.loads(form.get("allowed_updates", '["message"]')))
            result = [u for u in self.updates if wanted & (u.keys() - {"update_id"})]
            return httpx.Response(200, json={"ok": True, "result": result})
        ctype = request.headers.get("content-type", "")
        if ctype.startswith("multipart/"):
            fields = {"multipart": True, "bytes": len(request.content)}
            body = request.content.decode("latin-1")
            m = re.search(r'name="chat_id"\r\n\r\n([^\r]*)', body)
            fields["chat_id"] = m.group(1) if m else None
            c = re.search(r'name="caption"\r\n\r\n(.*?)\r\n--', body, re.S)
            fields["caption"] = c.group(1) if c else ""
        else:
            fields = dict(httpx.QueryParams(request.content.decode()))
        self.calls.append({"method": method, **fields})
        step = self.script.pop(0) if self.script else "ok"
        if isinstance(step, Exception):
            raise step
        if isinstance(step, int):
            return httpx.Response(step, text="error")
        if isinstance(step, dict):
            return httpx.Response(step.get("error_code", 400), json={"ok": False, **step})
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "message_id": uuid.uuid4().int % 10**9,
                    "chat": {"id": int(fields.get("chat_id") or 0), "type": "private"},
                },
            },
        )

    def client(self, token: str = MOCK_TOKEN) -> TelegramClient:
        return TelegramClient(httpx.Client(transport=httpx.MockTransport(self.handler)), token)

    def transport(self, token: str = MOCK_TOKEN) -> "tuple[TelegramTransport, TelegramClient]":
        client = self.client(token)
        return TelegramTransport(client, str(self.bot_id)), client


def make_telegram_transport(settings) -> "tuple[TelegramTransport, TelegramClient]":
    """Fake by default. Live api.telegram.org only when ALL hold: DESK_TELEGRAM_LIVE=1,
    a token in the environment, and the Bot API version pinned (RC12)."""
    from desk.config import TELEGRAM_BOT_API_VERSION, GateBlockedError

    tg = settings.telegram
    if not tg.live:
        if tg.bot_token:  # HIGH-1: a real bot's rows must never be "sent" by the fake
            raise GateBlockedError(
                "TELEGRAM_BOT_TOKEN is set but DESK_TELEGRAM_LIVE is not 1: refusing the "
                "in-memory fake. Unset the token for mock runs, or set DESK_TELEGRAM_LIVE=1."
            )
        transport, client = FakeTelegram().transport()
        transport.allowed_recipients = tg.allowed_chat_ids
        return transport, client
    if not tg.bot_token:
        raise GateBlockedError("DESK_TELEGRAM_LIVE=1 but TELEGRAM_BOT_TOKEN is not set")
    if not TELEGRAM_BOT_API_VERSION:
        raise GateBlockedError(
            "pin the Telegram Bot API version first (BOM.md, PC-SESSION-CHECKLIST.md step 2)"
        )
    client = TelegramClient(httpx.Client(), tg.bot_token)
    return TelegramTransport(client, tg.bot_id, tg.allowed_chat_ids), client
