# Implementation notes

One line per deviation from the approved plan: what changed and why.

- 2026-10-01 M0: Plan said "16 direct pins"; the spec actually lists 15. Corrected in BOM.md; no package added or removed.
- 2026-10-01 M0: Python 3.13.15 and PostgreSQL 17.11 could not be installed — this cloud env's network policy denies python.org, GitHub release downloads and apt.postgresql.org. Did NOT fall back to 3.13.14 / PG 16. Tests cannot run on the pinned stack until the owner allows those hosts.
- 2026-10-01 M0: psycopg installed without the `[binary]` extra (that would add an unpinned package); it uses the system libpq.
- 2026-10-01 M1: Official NSE holiday file (S30) NOT created — `www.nseindia.com` is blocked by this env's network policy, and holidays are not typed from memory. Loader + schema + MOCK test calendar built; pipeline refuses dates outside a calendar's coverage.
- 2026-10-01 M1: Bug found and fixed during smoke run — R14 put a futures-proxy level (VAH) and a cash-index level (prior high) in one sentence without saying which was which. Every level in a path now names its instrument; test `test_scenario_levels_always_name_their_instrument` guards it.
- 2026-10-01 M1: Bug found by tests — pydantic silently turned float 1.5 into Decimal. Facts now reject floats before conversion.
- 2026-10-01 M1: Value area now reports real price edges (VAL = bin lower edge, VAH = bin upper edge, POC = bin middle). Method version `VP-even-split-v1/VA70-single-row-v1/edges-v1`.
- 2026-10-01 M1: R13 Greeks shown as UNAVAILABLE (feed has none; pricing-model assumptions are an owner choice), so R13 is DEGRADED on every mock run. Honest, by design.
- 2026-10-01 M1: Golden-test tolerance is zero (exact Decimal match). Any change to a number = method change + version bump.
