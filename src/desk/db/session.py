"""Engine and session factory. One transaction per inbound message."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def make_engine(url: str) -> Engine:
    if not url.startswith("postgresql+psycopg://"):
        raise ValueError("DESK_DATABASE_URL must use postgresql+psycopg:// (pinned BOM)")
    return create_engine(url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
