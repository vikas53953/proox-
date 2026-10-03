# Build backlog (raised during v1.3 build)

Items the owner approved for later. Not v1.3 scope unless marked as a launch blocker.
Separate from the Future backlog document (F01-F22), which stays out of scope.

| ID | Item | Status | Depends on | Raised |
|---|---|---|---|---|
| B01 | Special Muhurat evening report (today: Muhurat day counts as the previous session for the next day, and gets **no** 08:45 report) | DEFERRED — separate item if wanted | Muhurat timing per year; owner decision | 2026-10-01 |
| B02 | Encrypt WhatsApp numbers (wa_id) at rest (also Telegram chat ids) | **BUILT behind DESK_ENCRYPT_SENDERS (OFF); key store still waits for G05 — stays a launch blocker until a real key store holds the key.** Must be ON, with the key in a real key store, before the first real invite goes out. | G05 key store / hosting decision | 2026-10-01 |
| B03 | Swap in the final brand font for generated media (charts: DejaVu placeholder; PDF: Helvetica placeholder; both visibly labelled `FONT: PLACEHOLDER`) | DEFERRED — at packaging time | Noto asset/licence/hash decision; shaping renderer for Hindi (G04) | 2026-10-01 |
| B04 | Per-customer setting for the 09:12 auction addendum (v1.3: goes to all opted-in customers when the deployment enables it) | DEFERRED — later iteration | Owner decision on the opt-in wording | 2026-10-01 |
| B05 | D07 export polish: a long bubble (e.g. TEXT R05) that cannot fit on a page leaves the previous page almost blank (print keeps each bubble whole). Option: allow very long bubbles to split, repeating the label line. | DEFERRED — cosmetic, owner nit (not a blocker) | None | 2026-10-01 |
| B06 | Key rotation for the B02 transport-id key (re-encrypt every covered column under a new key; today one key, no rotation) | DEFERRED | G05 key store; B02 | 2026-10-03 |
