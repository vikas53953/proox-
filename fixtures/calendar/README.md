# Calendars

| File | Status |
|---|---|
| `MOCK-test-calendar-2026.json` | Test fallback only. Synthetic dates. |
| `NSE-CM-holidays-<year>-v<n>.json` | **PENDING** — official NSE list (S30). Not fetched: `www.nseindia.com` is blocked by this environment's network policy. Holidays are not typed from memory. |

Schema: `version, segment, source_url, retrieved_at, is_mock, covers_from, covers_to,
holidays[{date, description}], special_sessions[{date, description}]`.
