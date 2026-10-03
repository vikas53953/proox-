# NSE public data — SHAPE SAMPLES

**SHAPE SAMPLES, hand-made, not downloaded from NSE, values fictional; re-verify field names against real files on the owner's PC before G03 closes.**

- Purpose: test `src/desk/feeds/nse_public.py` (G03 staged direction, step 2) without any
  network access. The adapter stays DISABLED while G03 is BLOCKED.
- Shapes follow NSE's public JSON pages as best documented (pre-open market, index option
  chain, FII/DII provisional cash activity). Field names may differ from the live files.
- Every number and every stock symbol (`SAMPLE_*`) is invented. Nothing here is market data.
- `fii_dii_provisional.json` is a bare JSON list (as NSE sends it), so it has no
  `_comment` label; this README is its label.
- The NSE CM holiday list is not repeated here: the calendar already lives in
  `fixtures/calendar/`.

| File (in `2026-10-01/`) | Mirrors | Maps to dataset |
|---|---|---|
| `pre_open_nifty.json` | pre-open market, key=NIFTY | `pre_open` (09:12 addendum) |
| `option_chain_nifty.json` | index option chain, symbol=NIFTY | `option_chain` (R13) |
| `fii_dii_provisional.json` | FII/DII provisional cash activity | `flows` (R10) |
