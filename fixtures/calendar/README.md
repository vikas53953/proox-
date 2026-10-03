# Calendars

| File | Status |
|---|---|
| `NSE-CM-holidays-2026-v1.json` | **Official.** NSE Capital Market 2026 trading holidays, transcribed by the owner from NSE circular CMTR71775 (12 Dec 2025) and the NSE holidays page (adds 15 Jan election holiday). Not scraped. Weekdays checked against the calendar. |
| `MOCK-test-calendar-2026.json` | Test fallback only. Synthetic dates. Never used for a real report. |

Rules:
- A new or corrected list = a new file with a new version (`-v2`), never an in-place edit.
- `holidays` holds weekday closures only; weekend holidays are kept under `weekend_holidays` for the record.
- `special_sessions` (e.g. Muhurat) count as trading days for "previous session", but get **no 08:45 report** until the owner decides otherwise. Muhurat 2026-11-08 timing is TBD.

Schema: `version, segment, source_url, retrieved_at, is_mock, covers_from, covers_to,
holidays[{date, description}], weekend_holidays[...], special_sessions[{date, description, timing_ist}]`.
