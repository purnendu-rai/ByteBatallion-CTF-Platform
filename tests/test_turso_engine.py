"""Turso (hosted SQLite) engine wiring (v3.2).

Covers URL normalisation, the fail-fast missing-token check, and a real
round-trip through the libsql dialect against a local libsql file — so
the dialect path is proven without needing network access or a token.
"""

import pytest
from sqlalchemy.orm import sessionmaker

from app.database import Base, _build_engine, _normalize_database_url
from app.models import User


def test_normalize_libsql_url():
    assert _normalize_database_url("libsql://db-x.turso.io") == (
        "sqlite+libsql://db-x.turso.io?secure=true")
    # Already-normalised URLs pass through untouched.
    assert _normalize_database_url(
        "sqlite+libsql://db-x.turso.io?secure=true") == (
            "sqlite+libsql://db-x.turso.io?secure=true")
    # Plain SQLite URLs are untouched.
    assert _normalize_database_url("sqlite:///./data/x.db") == (
        "sqlite:///./data/x.db")


def test_remote_libsql_without_token_refuses():
    with pytest.raises(RuntimeError, match="TURSO_AUTH_TOKEN"):
        _build_engine("libsql://db-x.turso.io")


def test_local_libsql_file_round_trips(tmp_path):
    """The app's own schema works on the libsql dialect: create the
    tables in a local libsql file, write a user, read it back."""
    db_file = tmp_path / "roundtrip.db"
    engine = _build_engine(f"sqlite+libsql:///{db_file}")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        session.add(User(username="libsqluser", email="libsql@example.com",
                         password_hash="x", role="player"))
        session.commit()
        found = session.query(User).filter(
            User.username == "libsqluser").first()
        assert found is not None
        assert found.email == "libsql@example.com"
    finally:
        session.close()
        engine.dispose()


def test_plain_sqlite_still_builds(tmp_path):
    engine = _build_engine(f"sqlite:///{tmp_path}/plain.db")
    assert engine is not None
    engine.dispose()
