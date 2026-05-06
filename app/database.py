"""SQLAlchemy engine + session factory."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from . import config


def _set_wal(dbapi_conn, _connection_record):
    """Enable WAL mode and a generous busy timeout on every new connection."""
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


def _migrate_db() -> None:
    """Forward-only migration: add new columns to existing tables.

    SQLite ALTER TABLE does not support IF NOT EXISTS, so we check
    PRAGMA table_info() and only add columns that are missing.
    """
    with engine.connect() as conn:
        pe_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(part_estimate)"))}
        for col_name, col_type in [
            ("net_value_usd", "FLOAT"),
            ("shipping_est_usd", "FLOAT"),
        ]:
            if col_name not in pe_cols:
                conn.execute(text("ALTER TABLE part_estimate ADD COLUMN " + col_name + " " + col_type))

        veh_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(vehicle)"))}
        if "gross_total_value" not in veh_cols:
            conn.execute(text("ALTER TABLE vehicle ADD COLUMN gross_total_value FLOAT"))

        tsp_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(top_sold_part)"))}
        if "sample_count" not in tsp_cols:
            conn.execute(text("ALTER TABLE top_sold_part ADD COLUMN sample_count INTEGER DEFAULT 1"))

        conn.commit()


def init_db() -> None:
    """Create tables if they don't exist, then apply forward migrations."""
    from . import models  # noqa: F401  (register models)

    models.Base.metadata.create_all(bind=engine)
    _migrate_db()


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
