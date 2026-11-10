"""SQLAlchemy engine / session / Base.

The same code runs on SQLite (dev) and PostgreSQL (later) — only the
DATABASE_URL changes. No SQLite-only SQL anywhere (brief, Section 3).
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


def _normalize_database_url(url: str) -> str:
    """Accept the URL forms the app supports.

    Turso's dashboard shows `libsql://<db>-<org>.turso.io`; SQLAlchemy
    needs the `sqlite+libsql://` scheme with `?secure=true` for remote
    connections, so rewrite it here instead of making the deploy
    remember the exact spelling.
    """
    if url.startswith("libsql://"):
        url = "sqlite+libsql://" + url[len("libsql://"):]
        if "?" not in url:
            url += "?secure=true"
        elif "secure=" not in url:
            url += "&secure=true"
    return url


def _build_engine(database_url: str | None = None,
                  turso_auth_token: str | None = None):
    url = _normalize_database_url(
        database_url if database_url is not None
        else get_settings().database_url)
    if url.startswith("sqlite+libsql://"):
        # Remote Turso database: the dialect needs the auth token, and a
        # missing token would only fail on first connect with a cryptic
        # error, so refuse to boot without one instead. A local libsql
        # file (sqlite+libsql:///path.db) or in-memory DB needs no token.
        from urllib.parse import urlparse

        is_remote = urlparse(url).hostname is not None
        token = (turso_auth_token if turso_auth_token is not None
                 else get_settings().turso_auth_token)
        if is_remote and not token:
            raise RuntimeError(
                "Refusing to start: DATABASE_URL is a remote Turso URL "
                "but TURSO_AUTH_TOKEN is not set.")
        connect_args = {"check_same_thread": False}
        if token:
            connect_args["auth_token"] = token
        return create_engine(url, connect_args=connect_args, future=True)
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

    `create_all` never alters an existing table, so a DB created before
    a column existed is missing it. These are additive-only, nullable
    columns, which SQLite and PostgreSQL both accept at any time — no data
    migration, no SQLite-only syntax.

    The ALTER is attempted unconditionally and "column already exists"
    is swallowed: a reflected column list can go stale (SQLAlchemy's
    Inspector caches per instance, and a pooled connection can hold a
    pre-DDL view), which used to make the check-then-ALTER race fail
    the whole boot with "duplicate column". Each ALTER runs in its own
    transaction so a swallowed failure cannot poison the next one
    (Postgres aborts the whole transaction on a failed statement).
    The swallow keys on the MESSAGE, not the exception type: plain
    SQLite raises OperationalError, but Turso's libsql dialect raises
    the same failure as `ValueError: Hrana: SQLite error: duplicate
    column name: ...` — catching only OperationalError crashed the
    live boot on 2026-11-10. Anything that is NOT a duplicate-column
    error still raises.
    """
    from sqlalchemy import text

    wanted = {
        "challenges": {
            "flag_sha256": "VARCHAR(64)",
            "max_attempts": "INTEGER",
        },
        # NOT NULL DEFAULT 0 so pre-existing solves read as False —
        # a plain nullable BOOLEAN would leave them NULL, and
        # `is_rejected IS FALSE` would then wrongly exclude every
        # solve made before this column existed.
        "solves": {"is_rejected": "BOOLEAN NOT NULL DEFAULT 0"},
    }
    for table, columns in wanted.items():
        for col, ddl in columns.items():
            try:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
            except Exception as exc:  # noqa: BLE001 — message decides, see above
                msg = str(exc).lower()
                if "duplicate column" in msg or "already exists" in msg:
                    continue  # column is already there — nothing to do
                raise
