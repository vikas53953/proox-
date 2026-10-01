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

SECRET_PATTERNS = [
    re.compile(r"EAA[A-Za-z0-9]{40,}"),  # Meta/Graph access token shape
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),  # generic provider API key shape
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key id
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


def test_http_routes_are_allowlisted():
    paths = {route.path for route in create_app().routes}
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
        text = path.read_text(errors="ignore")
        for pattern in SECRET_PATTERNS:
            assert not pattern.search(text), f"possible secret in {rel}"
