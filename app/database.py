"""SQLAlchemy engine + session factory."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from . import config


def _set_wal(dbapi_conn, _connection_record):
    """Enable WAL mode and a generous busy timeout on every new connection.

    WAL (Write-Ahead Logging) allows concurrent reads while a write is in
    progress, which prevents "database is locked" errors when the web server
    and a manual run_now process both access the DB at the same time.
    busy_timeout tells SQLite to retry for up to 30 s before raising an error.
    """
    dbapi_conn.execute("PRAGMA journal_mode=WAL")
    dbapi_conn.execute("PRAGMA busy_timeout=30000")


engine = create_engine(
    config.DB_URL,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False},
)
event.listen(engine, "connect", _set_wal)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    """Create tables if they don't exist."""
    from . import models  # noqa: F401  (register models)

    models.Base.metadata.create_all(bind=engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
