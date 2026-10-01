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

## 4. First live Telegram send

- [ ] `DESK_TELEGRAM_LIVE=1`; the startup check (`getMe`) succeeds.
- [ ] Only ONE `serve` per bot (a second poller gets 409 and `serve` stops).
- [ ] For mock runs, unset `TELEGRAM_BOT_TOKEN` (the fake refuses to start while it is set).
- [ ] Create a Telegram invite (24 h, single use) and open the printed t.me link.
- [ ] /start → welcome + separate opt-in; reply YES.
- [ ] Morning run delivers summary, PDF and chart; states show SENT
      ("Telegram reports no delivery or read receipts").
- [ ] Until G03 is decided, the report is MOCK / UNAVAILABLE — that is expected.
