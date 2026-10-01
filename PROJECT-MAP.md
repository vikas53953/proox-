# Project map

Plain-words role of every folder and key file. Refreshed each milestone (last: M2).

## Top level

| Path | What it does |
|---|---|
| `README.md` | One-page intro, how to run tests and print a mock report |
| `BOM.md` | The exact versions we are allowed to use, and which are blocked |
| `GATES.md` | The six open owner decisions (G01–G06) and the mock used meanwhile |
| `implementation-notes.md` | Log of every deviation from the plan, one line each, with the reason |
| `pyproject.toml` | Project settings: exact pins, test and lint settings |
| `requirements*.in` / `requirements*.lock` | Pinned direct packages / every package with checksums |
| `.env.example` | Setting names with empty placeholders — never real secrets |
| `alembic.ini`, `alembic/` | Database schema changes (migrations). `versions/0001_initial_schema.py` creates all M2 tables |
| `samples/` | Text output of mock reports, so you can read one without running anything |

## `src/desk/` — the program

| Path | What it does |
|---|---|
| `__main__.py` | `python -m desk report ...` prints a MOCK report; `python -m desk invite ...` creates an invite (operator only, code shown once) |
| `config.py` | Reads settings; refuses any real adapter while gates are blocked |
| `app.py` | Web front door: WhatsApp webhook + health check, nothing else |
| `tenancy.py` | The only way to read a tenant's data; another tenant's row looks exactly like "not found" |
| `db/models.py` | Database tables: invites, tenants, agent bindings, inbound messages, outbox, reports |
| `db/session.py` | Opens the database connection (PostgreSQL via psycopg only) |
| `transport/whatsapp/signature.py` | Checks Meta's signature on every incoming webhook (secret-based fingerprint) |
| `transport/whatsapp/payload.py` | Reads the incoming WhatsApp message; sender = transport id, never the display name |
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
| `test_e06_golden.py` | Maths gives the hand-checked answers; no peeking ahead; contradictions caught; no certainty words |

| `tests/whatsapp_helpers.py` | Builds signed fake WhatsApp webhooks for tests |
