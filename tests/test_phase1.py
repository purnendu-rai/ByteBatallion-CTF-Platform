"""Phase 1 acceptance tests: auth & roles.

Covers: register -> login -> profile shows; wrong password fails; a
player cannot open any /admin URL (403); sessions survive a server
restart. Plus: uniqueness, CSRF, login lockout, banned users, and
passwords stored only as bcrypt hashes.
"""

import re

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import User, UserSession
from app.security import SESSION_COOKIE, login_rate_limiter

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture(autouse=True)
def _isolate_auth_state():
    """Fresh users table + empty rate-limiter for every test in this module."""
    login_rate_limiter.reset_all()
    db = SessionLocal()
    try:
        db.query(UserSession).delete()
        db.query(User).delete()
        db.commit()
    finally:
        db.close()
    yield


def get_csrf(client: TestClient) -> str:
    # Works signed-in (redirects to /challenges, whose nav has the logout
    # form) and anonymous (the login form itself).
    resp = client.get("/auth/login", follow_redirects=True)
    match = CSRF_RE.search(resp.text)
    assert match, "csrf token not found on page"
    return match.group(1)


def register(client: TestClient, username: str, email: str | None = None,
             password: str = "password123"):
    return client.post(
        "/auth/register",
        data={
            "csrf_token": get_csrf(client),
            "username": username,
            "email": email or f"{username}@upsifs.in",
            "password": password,
        },
        follow_redirects=False,
    )


def logout(client: TestClient):
    return client.post(
        "/auth/logout", data={"csrf_token": get_csrf(client)}, follow_redirects=False
    )


def _set_field(username: str, **fields) -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username=username).first()
        assert user is not None
        for key, value in fields.items():
            setattr(user, key, value)
        db.commit()
    finally:
        db.close()


# --- core acceptance checks -------------------------------------------------

def test_register_login_profile(client):
    r = register(client, "alice")
    assert r.status_code == 303
    assert r.headers["location"] == "/challenges"

    r = client.get("/profile", follow_redirects=False)
    assert r.status_code == 200
    assert "alice" in r.text

    # logout, then log back in
    assert logout(client).status_code == 303
    assert client.get("/profile", follow_redirects=False).status_code == 302

    r = client.post(
        "/auth/login",
        data={"csrf_token": get_csrf(client), "username": "alice", "password": "password123"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    r = client.get("/profile", follow_redirects=False)
    assert r.status_code == 200
    assert "alice" in r.text


def test_wrong_password_fails(client):
    register(client, "carol")
    logout(client)
    r = client.post(
        "/auth/login",
        data={"csrf_token": get_csrf(client), "username": "carol", "password": "wrongwrong"},
    )
    assert "Incorrect username or password" in r.text
    assert client.get("/profile", follow_redirects=False).status_code == 302


def test_player_cannot_open_admin(client):
    register(client, "grace")
    assert client.get("/admin", follow_redirects=False).status_code == 403


def test_anonymous_cannot_open_admin(client):
    # Anonymous users are sent to login (with `next`), not shown a 403.
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/auth/login?next=")


def test_session_survives_restart(client):
    """Sessions live in the DB (v3.6), so a brand-new client holding a
    valid session cookie is still logged in after a restart — but a
    token that was never issued, or a tampered one, is not."""
    register(client, "iris")
    token = client.cookies.get(SESSION_COOKIE)
    assert token, "register did not set a session cookie"

    fresh = TestClient(app)
    fresh.cookies.set(SESSION_COOKIE, token)
    r = fresh.get("/profile", follow_redirects=False)
    assert r.status_code == 200
    assert "iris" in r.text

    # a token that was never issued must NOT work
    fresh.cookies.set(SESSION_COOKIE, "never-issued-token")
    assert fresh.get("/profile", follow_redirects=False).status_code == 302
    # ... and neither must a tampered real token
    fresh.cookies.set(SESSION_COOKIE, token[:-2] + "zz")
    assert fresh.get("/profile", follow_redirects=False).status_code == 302


# --- supporting behavior ----------------------------------------------------

def test_login_with_email(client):
    register(client, "bob", "bob@upsifs.in")
    logout(client)
    r = client.post(
        "/auth/login",
        data={"csrf_token": get_csrf(client), "username": "bob@upsifs.in",
              "password": "password123"},
        follow_redirects=False,
    )
    assert r.status_code == 303


def test_duplicate_username_and_email_rejected(client):
    assert register(client, "dave").status_code == 303
    logout(client)
    # PRG: the error is shown on the landing page after the redirect.
    r = register(client, "dave")
    assert r.status_code == 303
    assert "Username is already taken" in client.get(r.headers["location"]).text
    r = register(client, "dave2", email="dave@upsifs.in")
    assert r.status_code == 303
    assert "Email is already registered" in client.get(r.headers["location"]).text


def test_short_password_rejected(client):
    r = register(client, "erin", password="short")
    assert r.status_code == 303
    assert "at least 8 characters" in client.get(r.headers["location"]).text
    db = SessionLocal()
    try:
        assert db.query(User).filter_by(username="erin").first() is None
    finally:
        db.close()


def test_password_never_stored_plaintext(client):
    register(client, "frank")
    db = SessionLocal()
    try:
        stored = db.query(User).filter_by(username="frank").first().password_hash
    finally:
        db.close()
    assert stored != "password123"
    assert stored.startswith("$2b$")


def test_admin_can_open_admin(client):
    register(client, "henry")
    _set_field("henry", role="admin")  # only an admin can change roles (via DB here)
    assert client.get("/admin", follow_redirects=False).status_code == 200


def test_csrf_required(client):
    r = client.post("/auth/register", data={
        "username": "jack", "email": "jack@upsifs.in", "password": "password123",
    })
    assert r.status_code in (403, 422)  # no token at all

    token = get_csrf(client)
    r = client.post("/auth/register", data={
        "csrf_token": "tampered", "username": "jack",
        "email": "jack@upsifs.in", "password": "password123",
    })
    assert r.status_code == 403


def test_login_lockout_after_five_failures(client):
    register(client, "kate")
    logout(client)
    token = get_csrf(client)
    for _ in range(5):
        client.post("/auth/login", data={
            "csrf_token": token, "username": "kate", "password": "wrongwrong",
        })
    # even the CORRECT password is rejected while locked out
    r = client.post("/auth/login", data={
        "csrf_token": token, "username": "kate", "password": "password123",
    })
    assert "Too many failed attempts" in r.text
    assert client.get("/profile", follow_redirects=False).status_code == 302


def test_banned_user_cannot_log_in(client):
    register(client, "liam")
    _set_field("liam", is_banned=True)
    # existing session dies immediately
    assert client.get("/profile", follow_redirects=False).status_code == 302
    logout(client)
    r = client.post("/auth/login", data={
        "csrf_token": get_csrf(client), "username": "liam", "password": "password123",
    })
    assert "banned" in r.text.lower()
    assert client.get("/profile", follow_redirects=False).status_code == 302
