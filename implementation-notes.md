# Implementation notes

One line per deviation from the approved plan: what changed and why.

- 2026-10-01 M0: Plan said "16 direct pins"; the spec actually lists 15. Corrected in BOM.md; no package added or removed.
- 2026-10-01 M0: Python 3.13.15 and PostgreSQL 17.11 could not be installed — this cloud env's network policy denies python.org, GitHub release downloads and apt.postgresql.org. Did NOT fall back to 3.13.14 / PG 16. Tests cannot run on the pinned stack until the owner allows those hosts.
- 2026-10-01 M0: psycopg installed without the `[binary]` extra (that would add an unpinned package); it uses the system libpq.
- 2026-10-01 M1: Official NSE holiday file (S30) NOT created — `www.nseindia.com` is blocked by this env's network policy, and holidays are not typed from memory. Loader + schema + MOCK test calendar built; pipeline refuses dates outside a calendar's coverage.
- 2026-10-01 M1: Bug found and fixed during smoke run — R14 put a futures-proxy level (VAH) and a cash-index level (prior high) in one sentence without saying which was which. Every level in a path now names its instrument; test `test_scenario_levels_always_name_their_instrument` guards it.
- 2026-10-01 M1: Bug found by tests — pydantic silently turned float 1.5 into Decimal. Facts now reject floats before conversion.
- 2026-10-01 M1: Value area now reports real price edges (VAL = bin lower edge, VAH = bin upper edge, POC = bin middle). Method version `VP-even-split-v1/VA70-single-row-v1/edges-v1`.
- 2026-10-01 M1: R13 Greeks shown as UNAVAILABLE (feed has none; pricing-model assumptions are an owner choice). SUPERSEDED by M1c below.
- 2026-10-01 M1: Golden-test tolerance is zero (exact Decimal match). Any change to a number = method change + version bump.
- 2026-10-01 M1b: Official NSE CM 2026 holiday file added from owner-provided data (circular CMTR71775 + NSE holidays page). Weekdays verified. Special sessions (Muhurat 8 Nov, timing TBD) count as a trading day for "previous session" but get NO 08:45 report — conservative default, owner to confirm.
- 2026-10-01: Work runs in a cloud container. It cannot edit files on the owner's PC or change host/OS-level settings (e.g. hosts file, firewall); the network allowlist changes only via the environment settings, and appears to need a fresh session to take effect.
- 2026-10-01 M1c: Owner chose Black-Scholes for R13. Built in pure Decimal (50 digits, no floats, no new library): `quant/greeks.py`, version `bsm-european-v1`. Assumptions (builder's choice, printed next to every Greek): spot = prior close; vol = published ATM IV; rate and dividend yield must come from the feed WITH a source, else Greeks = UNAVAILABLE; time ACT/365 from data as-of to expiry 15:30 IST. Stale chain -> no Greeks. Cross-checked against Hull's textbook example and an independent float reference.
