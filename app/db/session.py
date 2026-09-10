from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine


def create_engine(database_url: str) -> AsyncEngine:
    engine = create_async_engine(database_url, hide_parameters=True)

    @event.listens_for(engine.sync_engine, "connect")
    def configure_sqlite(connection, _record):
        # Explicit transactions also cover DDL and savepoints on Python 3.11 SQLite.
        connection.isolation_level = None
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    @event.listens_for(engine.sync_engine, "begin")
    def begin(connection):
        connection.exec_driver_sql("BEGIN")

    return engine


def session_factory(engine: AsyncEngine):
    return async_sessionmaker(engine, expire_on_commit=False)
