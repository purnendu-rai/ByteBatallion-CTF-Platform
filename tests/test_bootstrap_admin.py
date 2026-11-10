"""First-admin bootstrap from BOOTSTRAP_ADMIN_* env vars (v3.1).

Covers the hosts without shell access (Render free tier): the app creates
the admin itself on startup when the three vars are set. The test user is
removed again at the end of the module so no other test sees it.
"""

import pytest

from app.config import Settings
from app.create_admin import bootstrap_admin_from_env
from app.database import SessionLocal
from app.models import User


def _settings(**overrides):
    values = {
        "bootstrap_admin_username": "bootadmin",
        "bootstrap_admin_email": "boot@example.com",
        "bootstrap_admin_password": "BootPass123!",
    }
    values.update(overrides)
    return Settings(**values)


def _delete_bootadmin():
    db = SessionLocal()
    try:
        db.query(User).filter(User.username == "bootadmin").delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    _delete_bootadmin()


def test_bootstrap_creates_admin_once():
    admin = bootstrap_admin_from_env(_settings())
    assert admin is not None
    assert admin.username == "bootadmin"
    assert admin.role == "admin"
    # Second call is a no-op — the username is already taken.
    assert bootstrap_admin_from_env(_settings()) is None
    db = SessionLocal()
    try:
        assert db.query(User).filter(User.username == "bootadmin").count() == 1
    finally:
        db.close()


def test_bootstrap_noop_when_unset():
    assert bootstrap_admin_from_env(Settings()) is None


def test_bootstrap_rejects_bad_values():
    assert bootstrap_admin_from_env(
        _settings(bootstrap_admin_password="short")) is None
    assert bootstrap_admin_from_env(
        _settings(bootstrap_admin_username="bad name!")) is None
    assert bootstrap_admin_from_env(
        _settings(bootstrap_admin_email="not-an-email")) is None


def test_bootstrap_does_not_overwrite_existing_email():
    first = bootstrap_admin_from_env(_settings())
    assert first is not None
    # Same email, different username: still a no-op, nothing overwritten.
    other = bootstrap_admin_from_env(_settings(
        bootstrap_admin_username="bootadmin2"))
    assert other is None
    db = SessionLocal()
    try:
        assert db.query(User).filter(
            User.email == "boot@example.com").count() == 1
    finally:
        db.close()
