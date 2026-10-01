# proox- — WhatsApp morning research desk (v1.3)

Report-only Indian pre-market research desk: Nifty 50 + liquid large caps, one 08:45 IST
report covering lenses R01–R15, delivered over WhatsApp. No trading, no broker, no
simulator, no app.

- Versions: see `BOM.md`. Open owner decisions: see `GATES.md`. File guide: `PROJECT-MAP.md`.

## Run the constitution tests (needs Python 3.13.15 + PostgreSQL 17.11)

```bash
python3.13 -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements-dev.lock
export DESK_TEST_DATABASE_URL=postgresql+psycopg://USER@localhost:5432/postgres  # admin URL
pytest tests/constitution        # DB tests skip if DESK_TEST_DATABASE_URL is unset
ruff check . && ruff format --check .
```

## Print a MOCK report

```bash
PYTHONPATH=src python -m desk report --scenario full_mock
PYTHONPATH=src python -m desk report --scenario no_orderflow_rights
PYTHONPATH=src python -m desk report --scenario auction_mock --kind AUCTION
PYTHONPATH=src python -m desk report --date 2026-10-02   # MOCK holiday -> no report
```

Pre-rendered copies are in `samples/`.

## Database

```bash
export DESK_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/desk
PYTHONPATH=src alembic upgrade head
PYTHONPATH=src python -m desk invite --phone-number-id <ID> --ttl-hours 72
```

## Where this runs

Built in a Claude Code cloud container. That container cannot edit files on your PC or
change host/OS-level settings; network access changes only via the environment settings.
