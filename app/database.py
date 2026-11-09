"""SQLAlchemy engine / session / Base.

The same code runs on SQLite (dev) and PostgreSQL (later) — only the
DATABASE_URL changes. No SQLite-only SQL anywhere (brief, Section 3).
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


def _build_engine():
    url = get_settings().database_url
    # FastAPI serves requests across threads; SQLite needs this flag.
    # The connect_arg is only added for SQLite — PostgreSQL ignores it.
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args, future=True)


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Base class for every ORM model."""


def get_db():
    """FastAPI dependency: one session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables from the models (dev bootstrap), then apply the
    tiny additive migrations this project needs (add a column if a table
    already existed from an earlier version)."""
    from app import models  # noqa: F401 — importing registers the models on Base

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """Add newly-introduced columns to already-existing tables.

    `create_all` never alters an existing table, so a DB created before a
    column existed is missing it. These are additive-only, nullable
    columns, which SQLite and PostgreSQL both accept at any time — no data
    migration, no SQLite-only syntax.
    """
    from sqlalchemy import inspect, text

    wanted = {"challenges": {"flag_sha256": "VARCHAR(64)"}}
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in wanted.items():
            if table not in existing_tables:
                continue
            present = {c["name"] for c in inspector.get_columns(table)}
            for col, ddl in columns.items():
                if col not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
