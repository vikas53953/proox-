# Bill of materials (v1.3)

Source of truth: Technical spec v1.3, "Pinned BOM" (registry reads 1 Oct 2026, S92–S108).
No style/equivalent/latest substitution. Any change here is an owner decision (RC12).
`tests/constitution/test_e01_scope.py` fails if the repo or installed env drifts from this.

## Platform

| Item | Pin | Status |
|---|---|---|
| Python | 3.13.15 | **BLOCKED (env)** — this cloud env's network denies python.org / GitHub release downloads; installed uv 0.8.17 has no 3.13.15 build. Container has 3.13.14 — not used as a substitute. |
| PostgreSQL | 17.11 | **BLOCKED (env)** — network denies apt.postgresql.org. Container has 16.14 — not used as a substitute. |
| Meta Graph / WhatsApp Cloud API | v26.0 | Pinned in code (`desk/config.py`); account BLOCKED G02 |

## Python direct pins (15)

| Package | Pin | Role | Group |
|---|---|---|---|
| fastapi | 0.142.2 | webhook server | runtime |
| pydantic | 2.13.5 | schemas | runtime |
| sqlalchemy | 2.1.1 | Postgres records | runtime |
| alembic | 1.20.0 | migrations | runtime |
| psycopg | 3.3.6 | Postgres driver (pure-Python; uses system libpq, no `[binary]` extra) | runtime |
| httpx | 0.28.1 | direct versioned REST (no WhatsApp/model SDK) | runtime |
| uvicorn | 0.54.0 | ASGI server | runtime |
| matplotlib | 3.11.2 | PNG charts | runtime |
| reportlab | 5.0.1 | PDFs | runtime |
| cryptography | 50.0.2 | secret envelope — **installed, unused until G05 key store** | runtime |
| Pillow | 12.3.0 | media | runtime |
| pytest | 9.1.1 | tests | dev |
| pytest-asyncio | 1.4.0 | async tests | dev |
| hypothesis | 6.168.3 | property tests | dev |
| ruff | 0.16.9 | lint/format | dev |

## Lock

| Item | Value |
|---|---|
| Lock tool | **uv 0.8.17, dev-only** (`uv pip compile --generate-hashes`), not a runtime dependency — owner-approved 1 Oct 2026 |
| Runtime lock | `requirements.lock` (target: CPython 3.13.15, x86_64 Linux) |
| Dev lock | `requirements-dev.lock` |
| Install | `pip install --require-hashes -r requirements-dev.lock` |

Lock resolution ≠ compatibility proof. Compatibility is verified only when the E01–E08 suite
passes on Python 3.13.15 + PostgreSQL 17.11.

## Fonts

| Use | Font | Status |
|---|---|---|
| Mock PDFs only | ReportLab built-in Helvetica, visibly labelled `FONT: PLACEHOLDER` | owner-approved placeholder |
| Mock charts (PNG) | Matplotlib's bundled DejaVu Sans, visibly labelled `FONT: PLACEHOLDER` | builder choice, same rule as the PDF placeholder — confirm |
| Real reports | Noto Sans / Noto Sans Devanagari | **BLOCKED** (asset revision/license/hash + shaping renderer, G04). `render_pdf` refuses non-mock reports; they go out as text parts. |
