"""v3.5 acceptance tests: email verification + password reset (Brevo).

The real Brevo API is never called here — send_email is monkeypatched
to record what would have been sent, and the recorded links are then
followed through the real routes.
"""

import re

import pytest
from fastapi.testclient import TestClient

from app import email_service
from app.database import SessionLocal
from app.models import User
from app.routes.auth import email_request_limiter
from app.security import login_rate_limiter

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
LINK_RE = re.compile(r'href="(https?://[^"]+)"')


@pytest.fixture(autouse=True)
def _isolate_email_state(monkeypatch):
    """Fresh users table, empty limiters, and a fake mail outbox.

    Verification is forced ON for this module even though no Brevo key
    is set (the app only enforces it when mail is configured)."""
    login_rate_limiter.reset_all()
    email_request_limiter.reset_all()
    monkeypatch.setattr("app.routes.auth._verification_enforced", lambda: True)
    db = SessionLocal()
    try:
        db.query(User).delete()
        db.commit()
    finally:
        db.close()
    outbox = []
    monkeypatch.setattr(email_service, "send_email", lambda *a, **k: outbox.append((a, k)) or True)
    yield outbox


def get_csrf(client: TestClient) -> str:
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
            "email": email or f"{username}@example.com",
            "password": password,
        },
        follow_redirects=False,
    )


def login(client: TestClient, username: str, password: str):
    return client.post(
        "/auth/login",
        data={
            "csrf_token": get_csrf(client),
            "username": username,
            "password": password,
        },
        follow_redirects=False,
    )


def _get_user(username: str) -> User:
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username=username).first()
        assert user is not None
        return user
    finally:
        db.close()


def _set_verified(username: str) -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username=username).first()
        user.is_verified = True
        db.commit()
    finally:
        db.close()


def _mail_link(outbox, index: int = 0) -> str:
    args, _kwargs = outbox[index]
    html = args[2]
    match = LINK_RE.search(html)
    assert match, "no link in sent email"
    return match.group(1)


# --- registration + verification -------------------------------------------

def test_register_sends_verification_and_blocks_login_until_verified(client, _isolate_email_state):
    outbox = _isolate_email_state
    r = register(client, "alice")
    assert r.status_code == 303
    assert r.headers["location"].startswith("/auth/login?msg=")
    assert "verification" in r.headers["location"].lower()
    assert _get_user("alice").is_verified is False
    assert len(outbox) == 1  # one verification mail
    assert outbox[0][0][1] == "Verify your ByteBatallion account"

    # Login is blocked while unverified.
    r = login(client, "alice", "password123")
    assert r.status_code == 303
    assert r.headers["location"].startswith("/auth/login?error=")

    # Clicking the link in the mail verifies + logs in.
    link = _mail_link(outbox)
    assert "/auth/verify?token=" in link
    r = client.get(link.replace("https://bytebatallion.onrender.com", ""), follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/challenges"
    assert _get_user("alice").is_verified is True

    # And now login works.
    r = login(client, "alice", "password123")
    assert r.status_code == 303
    assert r.headers["location"] == "/challenges"


def test_register_without_mail_config_behaves_as_before(client, _isolate_email_state, monkeypatch):
    """No Brevo key configured -> verification off, old behaviour:
    account usable immediately (this is what the rest of the suite
    relies on in dev/test)."""
    monkeypatch.setattr("app.routes.auth._verification_enforced", lambda: False)
    r = register(client, "dave")
    assert r.status_code == 303
    assert r.headers["location"] == "/challenges"
    assert _get_user("dave").is_verified is True


def test_bad_verification_token_goes_to_resend_page(client, _isolate_email_state):
    r = client.get("/auth/verify?token=garbage", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/auth/resend-verification?error=")


def test_resend_verification_sends_new_link(client, _isolate_email_state):
    outbox = _isolate_email_state
    register(client, "bob")
    assert len(outbox) == 1
    r = client.post(
        "/auth/resend-verification",
        data={"csrf_token": get_csrf(client), "email": "bob@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert len(outbox) == 2
    assert "/auth/verify?token=" in _mail_link(outbox, 1)


def test_resend_verification_unknown_email_sends_nothing(client, _isolate_email_state):
    outbox = _isolate_email_state
    r = client.post(
        "/auth/resend-verification",
        data={"csrf_token": get_csrf(client), "email": "nobody@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "msg=" in r.headers["location"]  # same generic message
    assert len(outbox) == 0


# --- forgot / reset password -------------------------------------------------

def test_forgot_password_resets_and_old_link_dies(client, _isolate_email_state):
    outbox = _isolate_email_state
    register(client, "carol")
    _set_verified("carol")

    r = client.post(
        "/auth/forgot-password",
        data={"csrf_token": get_csrf(client), "email": "carol@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert len(outbox) == 2  # verification mail from register + reset mail
    assert outbox[1][0][1] == "Reset your ByteBatallion password"
    link = _mail_link(outbox, 1)
    assert "/auth/reset-password?token=" in link
    path = link.replace("https://bytebatallion.onrender.com", "")

    # The reset form renders for a good token.
    r = client.get(path)
    assert r.status_code == 200
    assert "Choose a new password" in r.text

    # Submit the new password.
    token = path.split("token=")[1]
    r = client.post(
        "/auth/reset-password",
        data={
            "csrf_token": get_csrf(client),
            "token": token,
            "password": "newpassword123",
            "password_confirm": "newpassword123",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/auth/login?msg=")

    # New password works, old one does not. (Check the dead link first:
    # once logged in, the reset page redirects to /challenges.)
    # The same reset link is now dead (password changed since issue).
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/auth/forgot-password?msg=")

    assert login(client, "carol", "newpassword123").headers["location"] == "/challenges"
    assert login(client, "carol", "password123").headers["location"].startswith("/auth/login?error=")


def test_forgot_password_unknown_email_is_generic(client, _isolate_email_state):
    outbox = _isolate_email_state
    r = client.post(
        "/auth/forgot-password",
        data={"csrf_token": get_csrf(client), "email": "ghost@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "msg=" in r.headers["location"]
    assert len(outbox) == 0  # nothing sent, same message as the real case


def test_reset_password_bad_token_rejected(client, _isolate_email_state):
    r = client.get("/auth/reset-password?token=garbage", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/auth/forgot-password?msg=")
