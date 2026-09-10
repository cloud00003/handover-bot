"""Async SQLite migration environment, without Telegram startup or credentials."""

import asyncio

from alembic import context

from app.config import load_database_url
from app.db.base import Base
from app.db import models  # noqa: F401: register table metadata
from app.db.session import create_engine

config = context.config


def migrate(connection):
    context.configure(connection=connection, target_metadata=Base.metadata,
                      render_as_batch=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def migrate_online():
    engine = create_engine(config.get_main_option("sqlalchemy.url") or load_database_url())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(migrate)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(url=config.get_main_option("sqlalchemy.url") or load_database_url(),
                      target_metadata=Base.metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"}, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
elif config.attributes.get("connection") is not None:
    migrate(config.attributes["connection"])
else:
    asyncio.run(migrate_online())
