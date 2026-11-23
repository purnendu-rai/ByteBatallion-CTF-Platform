"""v3.6 acceptance tests: server-side sessions.

The bug (reported 2026-11-23): opening the site on a browser where
someone else had logged in silently signed you in AS that person, and
logging out did not kill the session — the old cookie stayed valid for
7 days. Root causes, all fixed here:

1. sessions were STATELESS signed cookies holding just {"uid": n} —
   nothing server-side to revoke;
2. logout only deleted the cookie in that one browser, and in prod the
   delete lacked the Secure flag, so some browsers kept the cookie;
3. only logged-in responses got `Cache-Control: no-store`, so a cached
   page could be shown to the next person on that computer.

Covered: logout kills the session server-side, login as a different
user from the same browser kills the previous session, password change
/ reset / "log out everywhere" kill other sessions, old stateless
tokens no longer work, and every non-static response is no-store with
Vary: Cookie.
"""

import re
import uuid

import pytest
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer

from app import email_service
from app.database import SessionLocal
from app.main import app
from app.models import User, UserSession
from app.routes.auth import email_request_limiter
from app.security import SESSION_COOKIE, login_rate_limiter

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
LINK_RE = re.compile(r'href="(https?://[^"]+)"')


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    login_rate_limiter.reset_all()
    email_request_limiter.reset_all()
    db = SessionLocal()
    try:
        db.query(UserSession).delete()
        db.query(User).delete()
        db.commit()
    finally:
        db.close()
    outbox = []
    monkeypatch.setattr(email_service, "send_email", lambda *a, **k: outbox.append((a, k)) or True)
    yield outbox


def _uniq(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def get_csrf(client: TestClient) -> str:
    resp = client.get("/auth/login", follow_redirects=True)
    match = CSRF_RE.search(resp.text)
    assert match, "csrf token not found on page"
    return match.group(1)


def register(client: TestClient, username: str, password: str = "password123"):
    return client.post(
        "/auth/register",
        data={
            "csrf_token": get_csrf(client),
            "username": username,
            "email": f"{username}@example.com",
            "password": password,
        },
        follow_redirects=False,
    )


def login(client: TestClient, username: str, password: str = "password123"):
    return client.post(
        "/auth/login",
        data={
            "csrf_token": get_csrf(client),
            "username": username,
            "password": password,
        },
        follow_redirects=False,
    )


def logout(client: TestClient):
    return client.post(
        "/auth/logout",
        data={"csrf_token": get_csrf(client)},
        follow_redirects=False,
    )


def _profile_status_with_cookie(token: str) -> int:
    fresh = TestClient(app)
    fresh.cookies.set(SESSION_COOKIE, token)
    return fresh.get("/profile", follow_redirects=False).status_code


def _live_sessions(username: str) -> int:
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username=username).first()
        assert user is not None
        return db.query(UserSession).filter(
            UserSession.user_id == user.id, UserSession.revoked_at.is_(None)
        ).count()
    finally:
        db.close()


# --- the reported bug -------------------------------------------------------


def test_logout_kills_session_server_side():
    """The core bug: a replayed cookie after logout must be dead."""
    client = TestClient(app)
    register(client, _uniq("alice"))
    token = client.cookies.get(SESSION_COOKIE)
    assert token
    assert client.get("/profile", follow_redirects=False).status_code == 200

    r = logout(client)
    assert r.status_code == 303
    # This browser is logged out...
    assert client.get("/profile", follow_redirects=False).status_code == 302
    # ...and the old cookie is dead in ANY browser (replay attack).
    assert _profile_status_with_cookie(token) == 302


def test_logout_delete_cookie_matches_set_flags():
    """The delete must carry the same flags as the set (path, HttpOnly,
    SameSite, Secure-in-prod) or browsers keep the cookie."""
    client = TestClient(app)
    register(client, _uniq("bob"))
    r = logout(client)
    delete = r.headers.get("set-cookie", "").lower()
    assert 'session=""' in delete or "session=" in delete
    assert "max-age=0" in delete
    assert "path=/" in delete
    assert "httponly" in delete
    assert "samesite=lax" in delete


def test_login_as_other_user_kills_previous_session():
    """Shared computer: A logged in, B logs in from the same browser —
    A's session must die, not linger for the next visit."""
    client = TestClient(app)
    alice = _uniq("alice")
    bob = _uniq("bob")
    register(client, alice)
    alice_token = client.cookies.get(SESSION_COOKIE)
    assert _profile_status_with_cookie(alice_token) == 200

    # Same browser, different user registers + logs in.
    register(client, bob)
    bob_token = client.cookies.get(SESSION_COOKIE)
    assert bob_token != alice_token
    # B is in, A is out — even with A's original cookie.
    assert _profile_status_with_cookie(bob_token) == 200
    assert _profile_status_with_cookie(alice_token) == 302
    assert _live_sessions(alice) == 0
    assert _live_sessions(bob) == 1


def test_old_stateless_token_no_longer_logs_in():
    """A v3.5-style signed {"uid": n} cookie must be worthless now —
    deploy logs everyone out once, which is the fix, not a side effect."""
    client = TestClient(app)
    user = _uniq("carol")
    register(client, user)
    db = SessionLocal()
    try:
        uid = db.query(User).filter_by(username=user).first().id
    finally:
        db.close()
    # The old token format, signed with the dev secret (the tests run
    # with the default dev secret — same construction as the old code).
    from app.config import get_settings

    old = URLSafeTimedSerializer(
        get_settings().secret_key, salt="bytebatallion-session"
    ).dumps({"uid": uid})
    assert _profile_status_with_cookie(old) == 302


def test_session_cookie_is_opaque():
    """The cookie must not contain a user id or any decodable payload."""
    client = TestClient(app)
    register(client, _uniq("dave"))
    token = client.cookies.get(SESSION_COOKIE)
    assert token
    assert "." not in token  # itsdangerous tokens have dots; random ones don't
    assert "uid" not in token


# --- password / session management ------------------------------------------


def test_password_change_kills_other_sessions_keeps_current():
    client = TestClient(app)
    user = _uniq("erin")
    register(client, user)
    # Second device: log in from another client.
    other = TestClient(app)
    assert login(other, user).status_code == 303
    other_token = other.cookies.get(SESSION_COOKIE)
    assert _profile_status_with_cookie(other_token) == 200

    r = client.post(
        "/settings/password",
        data={
            "csrf_token": get_csrf(client),
            "current_password": "password123",
            "new_password": "newpassword123",
            "confirm_password": "newpassword123",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    # This browser still works; the other device is dead.
    assert client.get("/profile", follow_redirects=False).status_code == 200
    assert _profile_status_with_cookie(other_token) == 302


def test_logout_all_kills_other_sessions_keeps_current():
    client = TestClient(app)
    user = _uniq("frank")
    register(client, user)
    other = TestClient(app)
    assert login(other, user).status_code == 303
    other_token = other.cookies.get(SESSION_COOKIE)

    r = client.post(
        "/auth/logout-all",
        data={"csrf_token": get_csrf(client)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/settings?msg=")
    assert client.get("/profile", follow_redirects=False).status_code == 200
    assert _profile_status_with_cookie(other_token) == 302


def test_password_reset_kills_all_sessions(_isolate):
    outbox = _isolate
    client = TestClient(app)
    user = _uniq("grace")
    register(client, user)
    token = client.cookies.get(SESSION_COOKIE)
    assert _profile_status_with_cookie(token) == 200

    anon = TestClient(app)
    r = anon.post(
        "/auth/forgot-password",
        data={"csrf_token": get_csrf(anon), "login": f"{user}@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert len(outbox) == 1
    link = LINK_RE.search(outbox[0][0][2]).group(1)
    reset_token = link.split("token=")[1]
    r = anon.post(
        "/auth/reset-password",
        data={
            "csrf_token": get_csrf(anon),
            "token": reset_token,
            "password": "brandnewpass1",
            "password_confirm": "brandnewpass1",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    # The pre-reset session is dead everywhere.
    assert _profile_status_with_cookie(token) == 302
    assert _live_sessions(user) == 0


# --- caching: no page may leak across users ----------------------------------


def test_no_store_on_logged_out_and_logged_in_pages():
    anon = TestClient(app)
    for path in ("/", "/auth/login"):
        r = anon.get(path)
        assert "no-store" in r.headers.get("cache-control", ""), path
        assert "cookie" in r.headers.get("vary", "").lower(), path
    client = TestClient(app)
    register(client, _uniq("heidi"))
    r = client.get("/profile")
    assert "no-store" in r.headers.get("cache-control", "")
    assert "cookie" in r.headers.get("vary", "").lower()
