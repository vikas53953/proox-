# NSE public data — SHAPE SAMPLES

**SHAPE SAMPLES, hand-made, not downloaded from NSE, values fictional; re-verify field names against real files on the owner's PC before G03 closes.**

- Purpose: test `src/desk/feeds/nse_public.py` (G03 staged direction, steps 2 and 2b) without any
  network access. The adapter stays DISABLED while G03 is BLOCKED.
- Shapes follow NSE's public JSON pages as best documented (pre-open market, index option
  chain, FII/DII provisional cash activity). Field names may differ from the live files.
- Every number and every stock symbol (`SAMPLE_*`) is invented. Nothing here is market data.
- `fii_dii_provisional.json` is a bare JSON list (as NSE sends it), and the two CSV files
  follow NSE's CSV layouts, so none of them has a `_comment` label; this README is their
  label.
- Step 2b files: real NSE names carry the date (`BhavCopy_NSE_CM_0_0_0_<YYYYMMDD>_F_0000.csv`,
  `fao_participant_oi_<DDMMYYYY>.csv`); here they are saved under fixed names in the report
  day's folder and hold the prior session (30 Sep 2026). The bhavcopy has rows outside the
  stock universe (`SAMPLE_SMALL`, a `BE` series row) to prove they are ignored; the stock
  universe stays the two `SAMPLE_*` names of the pre-open sample. Participant OI header
  cells keep the stray tabs seen in NSE's file; longs equal shorts market-wide.
- The NSE CM holiday list is not repeated here: the calendar already lives in
  `fixtures/calendar/`.

| File (in `2026-10-01/`) | Mirrors | Maps to dataset |
|---|---|---|
| `pre_open_nifty.json` | pre-open market, key=NIFTY | `pre_open` (09:12 addendum) |
| `option_chain_nifty.json` | index option chain, symbol=NIFTY | `option_chain` (R13) |
| `fii_dii_provisional.json` | FII/DII provisional cash activity | `flows` (R10) |
| `all_indices.json` | all indices (NIFTY 50 + SECTORAL INDICES rows) | `sectors` (R04) |
| `cm_bhavcopy.csv` | CM bhavcopy, UDiFF CSV columns | `stocks` (R05, prior-session OHLC) |
| `fao_participant_oi.csv` | F&O participant-wise open interest | `fno` (R09, participant OI only) |
