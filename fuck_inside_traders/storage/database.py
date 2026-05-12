from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from fuck_inside_traders.settings import get_settings
from fuck_inside_traders.storage.models import Base


def make_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args, future=True)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db(db_engine: Engine | None = None) -> None:
    active_engine = db_engine or engine
    Base.metadata.create_all(bind=active_engine)
    ensure_lightweight_schema(active_engine)


def ensure_lightweight_schema(db_engine: Engine) -> None:
    """Apply additive V1 schema updates without requiring Alembic yet."""
    inspector = inspect(db_engine)
    additions = {
        "prediction_market_snapshots": {
            "source": "VARCHAR(64) DEFAULT 'unknown'",
            "provider_kind": "VARCHAR(32) DEFAULT 'unknown'",
        },
        "asset_price_snapshots": {
            "source": "VARCHAR(64) DEFAULT 'unknown'",
            "provider_kind": "VARCHAR(32) DEFAULT 'unknown'",
        },
        "news_items": {
            "provider_kind": "VARCHAR(32) DEFAULT 'unknown'",
        },
        "polymarket_discovery_candidates": {
            "closed": "BOOLEAN DEFAULT FALSE",
        },
    }
    with db_engine.begin() as connection:
        for table_name, columns in additions.items():
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, ddl in columns.items():
                if column_name not in existing_columns:
                    connection.execute(
                        text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")
                    )


@contextmanager
def session_scope(session_factory: sessionmaker[Session] = SessionLocal) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
