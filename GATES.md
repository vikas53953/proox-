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
