# PC session checklist (owner's PC)

Things the cloud container cannot do (pinned versions and api.telegram.org are blocked
there). Do them in this order; stop at the first failure and report it.

## 1. Pinned stack (makes M0-M4 DONE)

- [ ] Python **3.13.15** installed; `python --version` shows exactly 3.13.15.
- [ ] PostgreSQL **17.11** running; `SHOW server_version;` shows 17.11.
- [ ] `pip install --require-hashes -r requirements-dev.lock` succeeds.
- [ ] `DESK_TEST_DATABASE_URL=... pytest tests/constitution` — **all** tests pass,
      including `test_python_is_exact_pin` and `test_postgres_server_is_exact_pin`.
- [ ] `ruff check . && ruff format --check .` clean.

## 2. Telegram Bot API version pin (RC12) — before the first live send

- [ ] Open https://core.telegram.org/bots/api#recent-changes and note the current
      "Bot API x.y" version and its date.
- [ ] Check the five methods we use still exist unchanged: `getMe`, `getUpdates`,
      `sendMessage`, `sendDocument`, `sendPhoto`.
- [ ] Write the version into `TELEGRAM_BOT_API_VERSION` in `src/desk/config.py` and into
      `BOM.md` (with date + source URL). Commit that change.
      Live Telegram refuses to start while this value is unset.

## 3. Bot token (test-only, interim until the G05 secret store)

- [ ] Create the bot with @BotFather; keep the token **only** in an environment variable
      on this PC: `TELEGRAM_BOT_TOKEN`, plus `TELEGRAM_BOT_USERNAME`.
- [ ] Never paste it into code, chat, logs, commits or screenshots. If it leaks, revoke it
      in @BotFather (/revoke) and make a new one.
- [ ] `pytest tests/constitution -k secret` passes (the scan flags token-shaped strings).

## 4. Safety settings (added 2026-10-03) — before the first live send

Do these before step 5. Each one is OFF or empty unless you set it.

### 4a. Mock mode: keep the WhatsApp token empty (B08)

- [ ] Do not set `WHATSAPP_ACCESS_TOKEN` on this PC. WhatsApp is mock only (G02).
      If it is set, `serve` stops with "refusing to start". Clear it and start again.
- [ ] If `WHATSAPP_PHONE_NUMBER_ID` is set, `serve` prints a note. Its messages stay
      queued. Nothing goes to WhatsApp.

### 4b. Encrypt chat ids and phone numbers at rest (B02)

- [ ] Make a key on this PC (it prints 86 characters):
      `python -c "import secrets; print(secrets.token_urlsafe(64))"`
- [ ] Keep the key only in an environment variable: `DESK_SENDER_KEY=<key>`.
      Also set `DESK_ENCRYPT_SENDERS=1`. Never paste the key into code, chat, logs,
      commits or screenshots.
- [ ] Keep one safe copy (for example a password manager). If you lose the key, the
      desk cannot read the stored ids again.
- [ ] Stop `serve`. Run `python -m desk senders status`.
      "MISMATCH" is normal here if the database already has plain ids.
- [ ] Run `python -m desk senders encrypt`. Then run `python -m desk senders status`
      again. It must show `plain=0` on every line and "consistent".
- [ ] `serve` refuses to start while the rows and the flag do not match. That is correct.
- [ ] Later: move the key into the G05 key store. B02 stays a launch blocker until then.

To change the key (rotation, B06):

- [ ] Stop `serve`. Make a new key with the same command.
- [ ] Set `DESK_SENDER_KEY_NEW=<new key>`. Keep `DESK_SENDER_KEY` as the old key.
- [ ] Run `python -m desk senders rotate`. It changes all rows in one step. If it stops
      with "refusing", nothing changed.
- [ ] Set `DESK_SENDER_KEY` to the new key. Clear `DESK_SENDER_KEY_NEW`.
- [ ] Run `python -m desk senders status`. It must show "consistent".

### 4c. Only your own Telegram chat (B07)

- [ ] Stop `serve`. Send any message (for example "hi") to your bot from your phone.
- [ ] Read your chat id from the bot's first update. The token stays in the environment
      variable, not in the command text:
      `python -c "import json,os,urllib.request; print([u['message']['chat']['id'] for u in json.load(urllib.request.urlopen('https://api.telegram.org/bot'+os.environ['TELEGRAM_BOT_TOKEN']+'/getUpdates'))['result'] if 'message' in u])"`
- [ ] The number in the list is your chat id. If the list is empty, `serve` already read
      your message: send a new one while `serve` is stopped, then run the command again.
- [ ] Set `TELEGRAM_ALLOWED_CHAT_IDS=<your chat id>` (more ids: separate with commas).
      Other chats then get only the neutral "invite not valid" reply.
- [ ] A wrong value (letters, only commas) makes `serve` refuse to start. Fix the value.
- [ ] Optional (T1 pre-binding): invite a person with `python -m desk invite --channel telegram --chat-id <their chat id>`; set `TELEGRAM_REQUIRE_PREBIND=1` to refuse invites without `--chat-id` (default 0; any value other than 0/1 refuses to start).

## 5. First live Telegram send

- [ ] `DESK_TELEGRAM_LIVE=1`; the startup check (`getMe`) succeeds.
- [ ] Only ONE `serve` per bot (a second poller gets 409 and `serve` stops).
- [ ] For mock runs, unset `TELEGRAM_BOT_TOKEN` (the fake refuses to start while it is set).
- [ ] Create a Telegram invite (24 h, single use) and open the printed t.me link.
- [ ] /start → welcome + separate opt-in; reply YES.
- [ ] Morning run delivers summary, PDF and chart; states show SENT
      ("Telegram reports no delivery or read receipts").
- [ ] Until G03 is decided, the report is MOCK / UNAVAILABLE — that is expected.
