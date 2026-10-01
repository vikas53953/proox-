"""E01 scope fixture — RC01 (report-only scope) and RC12 (exact pins, no silent choices).

Proves: the service has no app/broker/trade surface, the installed environment is exactly
the pinned BOM, open gates cannot be bypassed by config, and no secret is committed.
"""

import ast
import re
import subprocess
import sys
from importlib import metadata
from pathlib import Path

import pytest

from desk.app import create_app
from desk.config import GateBlockedError, load_settings

# Verbatim copy of the Technical spec v1.3 "Pinned BOM". This is the source of truth the
# repo files are checked against; editing it is an owner decision, not a refactor.
SPEC_PYTHON = (3, 13, 15)
SPEC_PINS = {
    "fastapi": "0.142.2",
    "pydantic": "2.13.5",
    "sqlalchemy": "2.1.1",
    "alembic": "1.20.0",
    "psycopg": "3.3.6",
    "httpx": "0.28.1",
    "uvicorn": "0.54.0",
    "matplotlib": "3.11.2",
    "reportlab": "5.0.1",
    "pytest": "9.1.1",
    "pytest-asyncio": "1.4.0",
    "hypothesis": "6.168.3",
    "cryptography": "50.0.2",
    "pillow": "12.3.0",
    "ruff": "0.16.9",
}

ALLOWED_ROUTES = {"/healthz", "/webhooks/whatsapp"}

# Module/package names that belong to deferred backlog items (F01-F12), never v1.3.
FORBIDDEN_MODULE_WORDS = {
    "broker",
    "brokers",
    "kite",
    "upstox",
    "angel",
    "fyers",
    "execution",
    "orders",
    "ticket",
    "tickets",
    "simulator",
    "paper",
    "lab",
    "backtest",
    "backtests",
    "strategy",
    "strategies",
    "portfolio",
    "pnl",
    "journal",
    "frontend",
    "webapp",
}
FORBIDDEN_IMPORTS = {
    "kiteconnect",
    "upstox_client",
    "smartapi",
    "fyers_apiv3",
    "celery",
    "redis",
    "apscheduler",
    "temporalio",
    "kafka",
}
FORBIDDEN_ROUTE_WORDS = ("order", "trade", "broker", "position", "portfolio", "login")

DATA_URI = re.compile(r"data:[\w/+.-]+;base64,[A-Za-z0-9+/=]+")
SECRET_PATTERNS = [
    re.compile(r"EAA[A-Za-z0-9]{40,}"),  # Meta/Graph access token shape
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),  # generic provider API key shape
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),  # Telegram bot token shape  # AWS access key id
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _read_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        m = re.match(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)", line)
        if m:
            pins[_norm(m.group(1))] = m.group(2)
    return pins


def _src_files(repo_root: Path) -> list[Path]:
    return sorted((repo_root / "src").rglob("*.py"))


# ---- RC12: exact pins -------------------------------------------------------------------


def test_python_is_exact_pin():
    assert sys.version_info[:3] == SPEC_PYTHON, (
        f"Python {sys.version.split()[0]} != pinned {'.'.join(map(str, SPEC_PYTHON))}"
    )


def test_requirement_files_match_spec_bom(repo_root):
    declared = _read_pins(repo_root / "requirements.in")
    declared |= _read_pins(repo_root / "requirements-dev.in")
    assert declared == SPEC_PINS


def test_lockfile_contains_every_spec_pin_with_hashes(repo_root):
    lock_text = (repo_root / "requirements-dev.lock").read_text()
    locked = _read_pins(repo_root / "requirements-dev.lock")
    for name, version in SPEC_PINS.items():
        assert locked.get(name) == version, f"{name} lock={locked.get(name)} spec={version}"
    # every locked requirement carries at least one sha256 hash
    blocks = re.split(r"\n(?=[a-z0-9])", lock_text)
    for block in blocks:
        if re.match(r"^[a-z0-9]", block):
            assert "--hash=sha256:" in block, f"unhashed lock entry: {block.splitlines()[0]}"


def test_installed_environment_equals_lockfile(repo_root):
    locked = _read_pins(repo_root / "requirements-dev.lock")
    mismatches = {}
    for name, version in locked.items():
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            installed = None
        if installed != version:
            mismatches[name] = (installed, version)
    assert not mismatches, f"installed != lock: {mismatches}"


# ---- RC01: report-only scope -----------------------------------------------------------


def _all_paths(routes, prefix: str = "") -> set[str]:
    """Walk every route, including routers nested via include_router. Fail closed on
    any route type we do not understand, so nothing can hide from the allowlist."""
    paths: set[str] = set()
    for route in routes:
        if hasattr(route, "path"):
            paths.add(prefix + route.path)
        elif hasattr(route, "original_router"):
            paths |= _all_paths(route.original_router.routes, prefix + route.include_context.prefix)
        else:
            raise AssertionError(f"unknown route type {type(route).__name__}")
    return paths


def test_http_routes_are_allowlisted():
    paths = _all_paths(create_app().routes)
    assert "/webhooks/whatsapp" in paths
    assert paths <= ALLOWED_ROUTES, f"unexpected routes: {paths - ALLOWED_ROUTES}"
    for path in paths:
        assert not any(word in path.lower() for word in FORBIDDEN_ROUTE_WORDS), path


def test_no_backlog_modules_in_source(repo_root):
    for path in _src_files(repo_root):
        parts = {_norm(p).removesuffix(".py") for p in path.relative_to(repo_root).parts}
        words = {w for part in parts for w in part.split("-")} | parts
        hit = words & FORBIDDEN_MODULE_WORDS
        assert not hit, f"{path}: backlog module name {hit}"


def test_no_forbidden_imports(repo_root):
    for path in _src_files(repo_root):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in FORBIDDEN_IMPORTS, f"{path}: imports {name}"


# ---- RC12 / G01-G06: open gates cannot be bypassed by config ---------------------------


@pytest.mark.parametrize(
    "env",
    [
        {"DESK_MODE": "live"},
        {"DESK_MODEL_ADAPTER": "anthropic"},
        {"DESK_FEED_ADAPTER": "nse_public"},
    ],
)
def test_config_refuses_to_close_gates(env):
    with pytest.raises(GateBlockedError):
        load_settings(env)


def test_default_config_is_mock():
    settings = load_settings({})
    assert (settings.mode, settings.model_adapter, settings.feed_adapter) == (
        "mock",
        "mock",
        "fixture",
    )


# ---- RC08: no secrets committed -------------------------------------------------------


def test_no_secrets_in_tracked_files(repo_root):
    tracked = subprocess.run(  # noqa: S603
        ["git", "ls-files", "-co", "--exclude-standard"],  # noqa: S607
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    for rel in tracked:
        path = repo_root / rel
        if not path.is_file() or path.suffix in {".png", ".pdf", ".lock"}:
            continue
        # Embedded base64 media (data: URIs) can match token shapes by chance; drop only
        # those blobs, the rest of the file is still scanned.
        text = DATA_URI.sub("data:", path.read_text(errors="ignore"))
        for pattern in SECRET_PATTERNS:
            assert not pattern.search(text), f"possible secret in {rel}"


def test_postgres_server_is_exact_pin(migrated):
    from sqlalchemy import text

    with migrated.connect() as conn:
        version = conn.execute(text("SHOW server_version")).scalar().split()[0]
    assert version == "17.11", f"PostgreSQL {version} != pinned 17.11"


def test_migrations_match_models(migrated):
    """The Alembic schema and the SQLAlchemy models must not drift apart."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from desk.db.models import Base

    with migrated.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_migrations_downgrade_and_upgrade_cleanly(pg_url):
    """Every migration can be undone and re-applied on a fresh database."""
    import os
    import uuid as _uuid

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    admin_url = os.environ["DESK_TEST_DATABASE_URL"]
    name = f"desk_rt_{_uuid.uuid4().hex[:8]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        repo = Path(__file__).resolve().parents[2]
        cfg = Config(str(repo / "alembic.ini"))
        cfg.set_main_option("script_location", str(repo / "alembic"))
        cfg.attributes["url"] = admin_url.rsplit("/", 1)[0] + f"/{name}"
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def test_secret_scan_still_sees_tokens_next_to_embedded_media():
    token = "EAA" + "B" * 50
    page = f'<img src="data:image/png;base64,EAA{"x" * 60}"> token={token}'
    cleaned = DATA_URI.sub("data:", page)
    assert any(p.search(cleaned) for p in SECRET_PATTERNS)
    assert not any(
        p.search(DATA_URI.sub("data:", page.replace(token, ""))) for p in SECRET_PATTERNS
    )


def test_secret_scan_flags_a_telegram_bot_token():
    fake = "1234567890:" + "AAH" + "x" * 32  # token-shaped, built at runtime, not a real one
    assert any(p.search(f"TELEGRAM_BOT_TOKEN={fake}") for p in SECRET_PATTERNS)
