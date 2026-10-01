# proox- — WhatsApp morning research desk (v1.3)

Report-only Indian pre-market research desk: Nifty 50 + liquid large caps, one 08:45 IST
report covering lenses R01–R15, delivered over WhatsApp. No trading, no broker, no
simulator, no app.

- Versions: see `BOM.md`. Open owner decisions: see `GATES.md`. File guide: `PROJECT-MAP.md`.

## Run the constitution tests (needs Python 3.13.15 + PostgreSQL 17.11)

```bash
python3.13 -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements-dev.lock
pytest tests/constitution
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
