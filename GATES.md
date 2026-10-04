# Open gates (all BLOCKED)

Owner rule (1 Oct 2026): G01–G06 stay BLOCKED. The builder never closes a gate.
The code enforces this: `desk/config.py` refuses any non-mock adapter at startup.

| Gate | What is undecided | What the code uses meanwhile |
|---|---|---|
| G01 | Model / customer access, adapter, account rights | `MockModelAdapter` (deterministic) |
| G02 | WhatsApp Business account, number, WABA, category, templates, billing | Mock transport; template drafts only |
| G03 | Licensed market data, provider, rights (real-time / EOD / AI-use / redistribution) | Dated fixture feed. See staged note below. |
| G04 | STT/TTS, audio codec/retention, full Hindi shaping | Transcript text only; audio part = `UNAVAILABLE: G04` |
| G05 | Hosting, region, image, secret store, SSL, blob, restore, cost | Local dev only; no provisioning |
| G06 | Pilot capacity, per-user VM allocation, recovery, entitlements | Single mock tenant fixtures |

## G03 staged direction (owner, 1 Oct 2026) — gate still BLOCKED

1. Mock fixture pipeline first.
2. Then an NSE public-published-data adapter (pre-open, option chain, FII/DII reports)
   behind the `FeedAdapter` interface.
3. Broker feeds (Kite/Upstox) later, plugging into the same interface without rework.

The NSE public adapter will be built and tested against saved sample files but stays
**disabled** until the owner confirms usage/redistribution/AI-use terms for that data.

- step 2 adapter built against hand-made shape samples, disabled (2026-10-03)
- step 2b: sectors/stocks/participant OI added on shape samples, disabled (2026-10-03)
- verify real files with `python -m desk nse verify <folder>` before G03 closes (offline
  check of hand-saved NSE files against the adapter's shapes; does not enable it) (2026-10-04)

## G02 verification checklist (must be ticked before any real WhatsApp send)

Approved as provisional by the owner on 2026-10-01; each item is re-checked against the
WhatsApp Business Platform / Cloud API documentation for **v26.0** when G02 is closed.

- [ ] Error-code groups in `transport/whatsapp/client.py`: rate/throughput = 4, 80007, 130429, 131048, 131056; 24h-window (re-engagement) = 131047; everything else 4xx = permanent failure; 5xx / timeout = UNKNOWN.
- [ ] `biz_opaque_callback_data` is accepted on text, template and media sends and is echoed back in status webhooks, so UNKNOWN sends can be reconciled (`outbox/receipts.py`).
- [ ] Status webhook shape (`statuses[].id/status/timestamp/recipient_id/errors`) matches `transport/whatsapp/payload.py`.
- [ ] Media upload (`/{phone_number_id}/media`) and document/image send payloads match `transport/whatsapp/client.py` (added in M4).
- [ ] Template names, languages, categories and APPROVED status come from the real account, not the drafts in `config/whatsapp_templates.json`.

## T1 — Telegram TEST transport (owner-approved 2026-10-01; not a product channel)

WhatsApp stays the product channel (specs unchanged). Telegram lets the first real-user
test (owner only) happen without waiting for G02.

| Rule | Status |
|---|---|
| Long polling only (`getUpdates`), no public endpoint, no new HTTP route | approved |
| Bot API version pinned in `config.TELEGRAM_BOT_API_VERSION` + BOM.md; live mode refuses to start until set | approved — **to do on the owner's PC** (PC-SESSION-CHECKLIST.md step 2) |
| Bot token: environment variable on the owner's PC only, test-only, interim until the G05 secret store; never in code, chat, logs, commits; E01 secret scan flags token shapes; logs are redacted | approved |
| Invite: deep link `t.me/<bot>?start=<code>`, single use, max 24 h | approved |
| B02 encryption at rest covers Telegram ids | approved (with B02) |
| **Before any user other than the owner:** an invite pre-binding rule for Telegram | built; **required by default (owner, 2026-10-04)** — `invite --channel telegram --chat-id <id>` binds the code to one chat (other chats: neutral reply, invite stays open); `TELEGRAM_REQUIRE_PREBIND` (default 1; 0 turns it off) refuses unbound Telegram invites in `invite` and onboarding |
| **Before any user other than the owner:** a privacy disclosure (Telegram bot chats are not end-to-end encrypted; Telegram stores them) | privacy disclosure — DRAFT built behind TELEGRAM_PRIVACY_NOTICE (off); wording pending owner |
| Chat-id allowlist (`TELEGRAM_ALLOWED_CHAT_IDS`, B07): chats not listed cannot bind an invite (neutral reply, invite not consumed); existing Telegram tenants not on the list are not handled and get no messages (checked right before each send); invalid values refuse to start | built; **locked by default (owner, 2026-10-04)**: unset/empty = no chat, `*` = any chat |
| Delivery truth: Telegram reports no delivery/read receipts — SENT is final; timeouts stay UNKNOWN (no reconcile) | built |
| Fake vs live: with a token set, the in-memory fake refuses to start (it could otherwise mark a real bot's rows SENT); each transport sends only rows of its own bot / phone id | built (security review HIGH-1/2) |
| **STOP cutoff:** STOP applies to every message whose send decision is made after the STOP is committed. Consent is checked per message right before its send; a message already handed to Telegram when STOP arrives completes, every later part is cancelled. So a STOP during a report can still let at most the part in flight through | built, tested (MED-7) |
| **24 h retention:** Telegram keeps unconfirmed messages for 24 h only. Messages sent to the bot while the desk is off for more than a day are lost (no error is shown to the sender) | known limit — owner to accept |
| **Blocking is noticed late:** a person blocking the bot is only seen on the next send to them (403 → opt-out). With `TELEGRAM_TRACK_MEMBER_UPDATES` (default 1 since 2026-10-04, owner; 0 turns it off) the poller also requests `my_chat_member`: a private-chat "kicked" update stops the opt-in at once (same as the 403 path) and cancels that tenant's pending proactive messages; unblocking does not resubscribe (START does) | early detection built, on by default (owner, 2026-10-04) |
| Poll errors: network / 5xx / 429 skip that poll only; 401/404 (token rejected) and 409 (another poller or a webhook on the bot) stop `serve` with a clear message; other cycle errors retry with backoff 5 s → 300 s cap | built (MED-5) |
