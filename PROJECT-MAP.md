# Project map

Plain-words role of every folder and key file. Refreshed each milestone.

| Path | What it does |
|---|---|
| `README.md` | One-page intro and how to run tests |
| `BOM.md` | The exact versions we are allowed to use, and which are blocked |
| `GATES.md` | The six open owner decisions (G01–G06) and the mock used meanwhile |
| `implementation-notes.md` | Log of any deviation from the plan, one line each, with the reason |
| `pyproject.toml` | Project settings: exact pins, test and lint settings |
| `requirements.in` / `requirements-dev.in` | The pinned direct packages, copied from the spec |
| `requirements.lock` / `requirements-dev.lock` | Every package incl. hidden dependencies, with checksums |
| `.env.example` | Names of settings, with empty placeholders — never real secrets |
| `.python-version` | Tells tools which Python to use (3.13.15) |
| `src/desk/__init__.py` | Marks the package; holds the spec version |
| `src/desk/config.py` | Reads settings from the environment; refuses any real adapter while gates are blocked |
| `src/desk/app.py` | The web server's front door: only health check now, WhatsApp webhook in M2 |
| `tests/conftest.py` | Shared test helpers (where the repo root is) |
| `tests/constitution/test_e01_scope.py` | Proves: no broker/app/trade code, exact pins, gates can't be bypassed, no secrets |
