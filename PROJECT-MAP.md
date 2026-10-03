# Project map

Plain-words role of every folder and key file. Refreshed each milestone (last: M4).

## Top level

| Path | What it does |
|---|---|
| `README.md` | One-page intro, how to run tests and print a mock report |
| `BOM.md` | The exact versions we are allowed to use, and which are blocked |
| `GATES.md` | The six open owner decisions (G01–G06) and the mock used meanwhile |
| `PC-SESSION-CHECKLIST.md` | What to do on the owner's PC: pinned stack, Telegram Bot API pin, bot token, first live send |
| `AUTOMATION.md` | How the @claude GitHub Action is used, its guardrails, and the owner-approval rule for merges |
| `.github/workflows/claude.yml` | The GitHub Action itself (inactive until merged into `main`) |
| `BACKLOG.md` | Approved later items found during the build (B01 Muhurat report, B02 encrypt numbers — launch blocker) |
| `implementation-notes.md` | Log of every deviation from the plan, one line each, with the reason |
| `pyproject.toml` | Project settings: exact pins, test and lint settings |
| `requirements*.in` / `requirements*.lock` | Pinned direct packages / every package with checksums |
| `.env.example` | Setting names with empty placeholders — never real secrets |
| `alembic.ini`, `alembic/` | Database schema changes. `0001` = M2 tables; `0002` = jobs, delivery states, receipts; `0003` = media, feedback, corrections |
| `config/whatsapp_templates.json` | Template drafts from the design doc — status DRAFT, not submitted (G02) |
| `uat/` | D07 run: `mock_days.py` plays 3 mock days end to end; `render_html.py` makes the chat page you review; `samples/uat/` holds the forwardable HTML + PDF |
| `samples/` | Mock reports as text, plus `samples/media/`: the PDF, the chart PNG and its manifest — open them directly |

## `src/desk/` — the program

| Path | What it does |
|---|---|
| `__main__.py` | `python -m desk report ...` prints a MOCK report; `python -m desk invite ...` creates an invite (operator only, code shown once) |
| `config.py` | Reads settings; refuses any real adapter while gates are blocked |
| `app.py` | Web front door: WhatsApp webhook + health check, nothing else |
| `jobs/scheduler.py` | Writes one dated job per day (07:30 start, 08:45 target, 09:15 hard stop); holidays get a SKIPPED row with the reason |
| `jobs/queue.py` | Hands a job to one worker at a time; a numbered "lease" stops an old worker from publishing |
| `jobs/worker.py` | Runs one job: build report → in one go mark DONE + save + queue parts; on failure retry, then tell users plainly |
| `outbox/parts.py` | Delivery plan: 1 summary text, 2 PDF, 3 chart — every part/caption repeats id, version, date, as-of, part i/n; text parts if no PDF |
| `outbox/notices.py` | Queues report parts and the "no report today" notice (fixed wording, no internal errors) |
| `outbox/policy.py` | Right before sending: opted in? today's report? inside 24h window? approved template? else wait/cancel |
| `outbox/sender.py` | Sends queued messages; a send with no clear answer becomes UNKNOWN and is never resent blindly |
| `outbox/receipts.py` | Meta's delivery receipts → SENT / DELIVERED / READ / FAILED, only ever forward |
| `render/palette.py` | Design-doc colours + a contrast calculator (text >= 4.5:1, marks >= 3:1) |
| `render/charts.py` | Sector-returns chart (PNG) from sourced facts only; refuses clipped/overlapping text; writes a manifest |
| `render/pdf.py` | Full R01-R15 PDF: header/footer + "page X of Y" on every page; refuses real reports while the font is blocked |
| `outbox/addendum.py` | The 09:12 "indicative auction" update message; expires at 09:15 |
| `feedback.py` | USEFUL / NOT USEFUL / FEEDBACK text, and operator corrections that never change the original report |
| `tenancy.py` | The only way to read a tenant's data; another tenant's row looks exactly like "not found" |
| `db/models.py` | Database tables: invites, tenants, agent bindings, inbound messages, outbox, reports |
| `db/session.py` | Opens the database connection (PostgreSQL via psycopg only) |
| `transport/whatsapp/signature.py` | Checks Meta's signature on every incoming webhook (secret-based fingerprint) |
| `transport/whatsapp/payload.py` | Reads the incoming WhatsApp message; sender = transport id, never the display name |
| `transport/base.py` | What every channel must offer + each channel's rules (window, templates, receipts) |
| `transport/whatsapp/adapter.py` | WhatsApp behind that common interface |
| `transport/telegram/client.py` | Telegram TEST transport: Bot API calls, in-memory fake, live-mode gate |
| `transport/telegram/poller.py` | Reads new Telegram messages (long polling) into the same onboarding/chat code |
| `runner.py` | PC test loop: poll Telegram -> plan today -> run worker -> send |
| `logsafe.py` | Scrubs tokens from every log line (Telegram's token sits in its URLs) |
| `transport/whatsapp/client.py` | Talks to the WhatsApp send API; only the in-memory fake exists while G02 is blocked |
| `transport/whatsapp/templates.py` | List of message templates and their Meta status; only APPROVED ones may be used |
| `transport/whatsapp/webhook.py` | The webhook itself: size limit → signature → parse → one transaction per message |
| `onboarding/invites.py` | Makes invite codes; stores only their fingerprint (hash); hides codes in stored text |
| `onboarding/service.py` | One message in: dedupe → existing tenant? reply : try invite → welcome + opt-in question |
| `onboarding/messages.py` | The reply texts (Roman Hinglish), from the design doc drafts |
| `agents/tools.py` | Which role may use which tool; anything requested by outside text is refused |
| `market_calendar.py` | Is today a trading day? What is 08:45 / 09:12 IST in UTC? Previous trading day |
| `pipeline.py` | The whole morning run: calendar → collect data → R01–R15 → review → report |
| `core/facts.py` | A **Fact** (value + source + time + instrument + unit) and a **Gap** (missing, with a reason, no value) |
| `core/lens.py` | The 15 lens names and a lens result; status (COMPLETE/DEGRADED/UNAVAILABLE) is calculated, never typed in |
| `core/scenario.py` | Base/up/down paths; blocks words like "guaranteed" and "70% chance" |
| `feeds/base.py` | The plug socket every data feed must fit (fixture now, NSE public next, broker later) + what each feed has rights to |
| `feeds/fixture.py` | Reads the MOCK files in `fixtures/market/` |
| `quant/bars.py` | Price bars; drops any bar not finished by the cutoff (no peeking ahead) |
| `quant/levels.py` | Prior-day high / low / close |
| `quant/profile.py` | Volume profile: POC and 70% value area |
| `quant/options.py` | Put/call ratio, max pain, ATM strike, basis, OI change |
| `quant/greeks.py` | Black-Scholes option Greeks (delta, gamma, vega, theta) with every assumption labelled |
| `quant/orderflow.py` | Tick-rule order-flow estimate (always labelled PROXY) |
| `lenses/context.py` | What every lens receives, and the "is this data fresh?" rules |
| `lenses/r01_news.py` … `r15_quality.py` | One file per lens R01–R15 |
| `lenses/bars_io.py` | Turns bar records from a feed into price bars |
| `agents/research.py` | Research role: collects every dataset for the day |
| `agents/model.py` | Model plug socket + the mock model (real model blocked by G01) |
| `agents/reviewer.py` | Reviewer role: checks before a report is allowed out |
| `report/model.py` | The report record (id, date, cutoff, version, 15 lenses, paths, gaps, hash) |
| `report/assemble.py` | Refuses a report with a missing lens; builds the top-gaps list; stamps the hash |
| `report/text.py` | Turns a report into plain text (WhatsApp parts / PDF come in M4) |

## `fixtures/` — test inputs

| Path | What it does |
|---|---|
| `calendar/NSE-CM-holidays-2026-v1.json` | Official NSE 2026 holidays (from your list) — used by `python -m desk report` |
| `calendar/MOCK-test-calendar-2026.json` | Fake holiday list for tests only |
| `calendar/README.md` | Says the official NSE calendar file is still pending, and why |
| `market/2026-10-01/full_mock/` | Every dataset for one fake morning |
| `market/2026-10-01/stale_quote/` | Same, but the S&P 500 quote is two days old |
| `market/2026-10-01/no_orderflow_rights/` | Same, but the feed has no right to trade data |
| `market/2026-10-01/prior_session_stale/` | Same, but the sector file is from the wrong day |
| `market/2026-10-01/malicious_source/` | Adds a news item that tries to give orders (prompt injection) |
| `market/2026-10-01/auction_mock/` | Adds 09:08 pre-open data for the 09:12 addendum |

## `tests/constitution/` — proof of the rules

| Path | What it proves |
|---|---|
| `test_e01_scope.py` | No broker/app/trade code; exact Python + PostgreSQL versions; migrations match tables; gates can't be bypassed; no secrets |
| `test_e02_onboarding.py` | Signed webhook → exactly one tenant + one welcome; replays, races, bad/expired/forwarded invites leak nothing |
| `test_e03_report.py` | All 15 lenses; every fact sourced; missing data shown as missing, never as a number |
| `test_e04_isolation.py` | Tenants can't see each other; injected text can't trigger tools; secrets never shown |
| `test_e05_delivery.py` | Report → parts → accepted/delivered/read/failed/UNKNOWN; 24h window and template rules; stale never sent |
| `test_t01_telegram.py` | Telegram: deep-link invite, no window/templates, SENT is final, retry-after, blocked = opt-out, /stop wins, token never in logs |
| `test_e08_followup.py` | STOP/START, 09:12 addendum (and its 09:15 expiry), TEXT command, feedback, corrections |
| `test_d03_media.py` | Real pixel + PDF checks: 390 px preview, greyscale, opaque, labels, manifest, page X of Y, searchable text |
| `test_e06_golden.py` | Maths gives the hand-checked answers; no peeking ahead; contradictions caught; no certainty words |

| `tests/whatsapp_helpers.py` | Builds signed fake WhatsApp webhooks for tests |
| `tests/constitution/test_e07_failures.py` | Worker crash, stale lease, feed/DB/budget failure, late and missed-deadline cases are all visible, never silent |
| `tests/delivery_helpers.py` | Fake clock, quick tenants and worker setup for delivery tests |
| `tests/media_helpers.py` | Reads text out of our PDFs without extra libraries (proves the text is searchable) |
