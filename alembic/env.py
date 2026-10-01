"""Alembic environment: URL from DESK_DATABASE_URL, schema from desk.db.models."""

import os

from alembic import context
from sqlalchemy import create_engine

from desk.db.models import Base

target_metadata = Base.metadata


def run_migrations_online() -> None:
    url = context.config.attributes.get("url") or os.environ["DESK_DATABASE_URL"]
    engine = create_engine(url)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
