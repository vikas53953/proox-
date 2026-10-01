import os
import secrets
import uuid
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from desk.agents.model import MockModelAdapter
from desk.config import Settings, WhatsAppSettings
from desk.db.models import Base
from desk.db.session import make_engine, make_session_factory
from desk.feeds.fixture import FixtureFeed
from desk.lenses.context import ReportKind
from desk.market_calendar import TradingCalendar
from desk.pipeline import run_report

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "fixtures"
MOCK_DAY = date(2026, 10, 1)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def mock_calendar() -> TradingCalendar:
    return TradingCalendar.load(FIXTURES / "calendar" / "MOCK-test-calendar-2026.json")


def feed(scenario: str, day: date = MOCK_DAY) -> FixtureFeed:
    return FixtureFeed(FIXTURES / "market", day, scenario)


@pytest.fixture(scope="session")
def build_report(mock_calendar):
    def _build(
        scenario: str = "full_mock", kind: ReportKind = ReportKind.MORNING, day: date = MOCK_DAY
    ):
        return run_report(
            calendar=mock_calendar,
            feed=feed(scenario),
            model=MockModelAdapter(),
            trading_date=day,
            kind=kind,
        )

    return _build


# ---- PostgreSQL (needed from M2) --------------------------------------------------------
# Tests that need a database use DESK_TEST_DATABASE_URL (an admin URL). Each run creates
# a fresh throwaway database, migrates it with Alembic, and drops it afterwards.


@pytest.fixture(scope="session")
def pg_url():
    admin_url = os.environ.get("DESK_TEST_DATABASE_URL")
    if not admin_url:
        pytest.skip("needs PostgreSQL: set DESK_TEST_DATABASE_URL")
    name = f"desk_test_{uuid.uuid4().hex[:10]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = admin_url.rsplit("/", 1)[0] + f"/{name}"
    yield url
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(scope="session")
def migrated(pg_url):
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.attributes["url"] = pg_url
    command.upgrade(cfg, "head")
    engine = make_engine(pg_url)
    yield engine
    engine.dispose()


@pytest.fixture
def db(migrated):
    factory = make_session_factory(migrated)
    yield factory
    with migrated.begin() as conn:
        # every table in the schema (never a hand-kept list: new tables can't be missed)
        tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture
def wa_settings() -> WhatsAppSettings:
    # Random per-run test secrets: nothing secret is ever written in the repo.
    return WhatsAppSettings(
        phone_number_id="100200300",
        waba_id="900800700",
        app_secret=secrets.token_hex(32),
        verify_token=secrets.token_hex(16),
        access_token=secrets.token_hex(24),
    )


@pytest.fixture
def settings(wa_settings) -> Settings:
    return Settings(
        mode="mock",
        database_url=None,
        model_adapter="mock",
        feed_adapter="fixture",
        whatsapp=wa_settings,
    )
