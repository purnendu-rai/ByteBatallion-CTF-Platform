"""Shared pytest setup, fixtures, and common helpers for ByteBatallion tests.

Point DATABASE_URL at a throwaway test DB BEFORE the app is imported,
so tests never touch the development database.
"""

import os
import re
import shutil
from pathlib import Path

# Use a test database
TEST_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "test_bytebatallion.db"
if TEST_DB_PATH.exists():
    try:
        TEST_DB_PATH.unlink()
    except OSError:
        pass
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import SessionLocal, init_db  # noqa: E402
from app.flag_engine import DATA_DIR  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Announcement,
    Challenge,
    CheatLog,
    ConfigEntry,
    HintUnlock,
    Instance,
    Page,
    Solve,
    Submission,
    Team,
    TeamMembership,
    User,
)
from app.security import (  # noqa: E402
    hash_password,
    login_rate_limiter,
    submission_rate_limiter,
)

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"
CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture(autouse=True, scope="session")
def _create_tables():
    """Ensure schema exists for the entire test session."""
    init_db()
    yield


@pytest.fixture(autouse=True)
def _isolate_db_state():
    """Wipe database rows and reset rate limiters before and after each test."""
    login_rate_limiter.reset_all()
    submission_rate_limiter.reset_all()
    db = SessionLocal()
    try:
        db.query(Solve).delete()
        db.query(Submission).delete()
        db.query(CheatLog).delete()
        db.query(HintUnlock).delete()
        db.query(Instance).delete()
        db.query(TeamMembership).delete()
        db.query(Team).delete()
        db.query(Announcement).delete()
        db.query(Page).delete()
        db.query(ConfigEntry).delete()
        db.query(Challenge).delete()
        db.query(User).delete()
        db.commit()
    finally:
        db.close()
    yield
    login_rate_limiter.reset_all()
    submission_rate_limiter.reset_all()
    # Clean up any files generated during test runs
    chal_dir = DATA_DIR / "challenges"
    if chal_dir.exists():
        for p in chal_dir.iterdir():
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
    inst_dir = DATA_DIR / "instances"
    if inst_dir.exists():
        for p in inst_dir.iterdir():
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


def get_csrf(client: TestClient, path: str = "/auth/login") -> str:
    """Extract CSRF token from page HTML or the csrf_token cookie."""
    resp = client.get(path, follow_redirects=True)
    match = CSRF_RE.search(resp.text)
    if match:
        return match.group(1)
    cookie_token = client.cookies.get("csrf_token")
    if cookie_token:
        return cookie_token
    raise ValueError(f"no csrf token found on {path}")


def register(
    client: TestClient,
    username: str,
    password: str = "password123",
    email: str | None = None,
):
    return client.post(
        "/auth/register",
        data={
            "csrf_token": get_csrf(client, "/auth/login"),
            "username": username,
            "email": email or f"{username}@upsifs.in",
            "password": password,
        },
        follow_redirects=False,
    )


def login(client: TestClient, username: str, password: str = "password123"):
    r = client.post(
        "/auth/login",
        data={
            "csrf_token": get_csrf(client, "/auth/login"),
            "username": username,
            "password": password,
        },
        follow_redirects=False,
    )
    return r


def logout(client: TestClient):
    return client.post(
        "/auth/logout",
        data={"csrf_token": get_csrf(client, "/challenges")},
        follow_redirects=False,
    )


def make_db_user(
    db_or_username, username_or_role: str = "admin", role: str = "admin"
) -> User:
    """Helper accepting either (db, username, role) or (username, role)."""
    if hasattr(db_or_username, "query"):
        db = db_or_username
        uname = username_or_role
        r = role
        existing = db.query(User).filter_by(username=uname).first()
        if existing:
            existing.role = r
            db.commit()
            db.refresh(existing)
            return existing
        user = User(
            username=uname,
            email=f"{uname}@upsifs.in",
            password_hash=hash_password("password123"),
            role=r,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    else:
        uname = db_or_username
        r = username_or_role
        db = SessionLocal()
        try:
            existing = db.query(User).filter_by(username=uname).first()
            if existing:
                existing.role = r
                db.commit()
                db.refresh(existing)
                return existing
            user = User(
                username=uname,
                email=f"{uname}@upsifs.in",
                password_hash=hash_password("password123"),
                role=r,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            return user
        finally:
            db.close()


def make_admin_client(username: str = "admin1") -> TestClient:
    make_db_user(username, role="admin")
    c = TestClient(app)
    r = login(c, username, "password123")
    assert r.status_code == 303, f"admin login failed: {r.status_code}"
    return c


def user_id(username: str) -> int:
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username=username).first()
        assert user is not None, f"user {username} not found"
        return user.id
    finally:
        db.close()
