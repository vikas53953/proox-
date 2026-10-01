# Implementation notes

One line per deviation from the approved plan: what changed and why.

- 2026-10-01 M0: Plan said "16 direct pins"; the spec actually lists 15. Corrected in BOM.md; no package added or removed.
- 2026-10-01 M0: Python 3.13.15 and PostgreSQL 17.11 could not be installed — this cloud env's network policy denies python.org, GitHub release downloads and apt.postgresql.org. Did NOT fall back to 3.13.14 / PG 16. Tests cannot run on the pinned stack until the owner allows those hosts.
- 2026-10-01 M0: psycopg installed without the `[binary]` extra (that would add an unpinned package); it uses the system libpq.
