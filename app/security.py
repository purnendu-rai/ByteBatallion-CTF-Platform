"""Security primitives: password hashing, signed sessions, CSRF, rate
limiting, and access-control dependencies (Phase 1).

Rules from the brief:
- passwords: bcrypt, never stored or logged in plaintext
- session cookie: signed, HttpOnly, Secure in production, 7-day expiry
- login rate limit: 5 failed tries -> 15-minute lockout
- randomness: `secrets` only, never `random`
"""

import logging
import secrets
import threading
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Form, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.context import CryptContext

from urllib.parse import quote

from app.config import get_settings
from app.database import SessionLocal
from app.models import User, UserSession

settings = get_settings()

# passlib 1.7.4 logs a harmless "trapped" warning with bcrypt >= 4.1
# (it cannot read bcrypt's version string). Silence that noise.
logging.getLogger("passlib").setLevel(logging.ERROR)

SESSION_COOKIE = "session"
SESSION_MAX_AGE = 7 * 24 * 60 * 60  # 7 days, in seconds
CSRF_COOKIE = "csrf_token"


# --- password hashing (bcrypt) -------------------------------------------

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _pwd_context.verify(password, password_hash)
    except ValueError:
        # Malformed/corrupted hash in the DB — treat as a failed login.
        return False


# --- server-side sessions (v3.6) -------------------------------------------
#
# v3.5 and earlier used a STATELESS signed cookie holding just
# `{"uid": n}`. That was the shared-browser bug: the token stayed valid
# for 7 days whatever happened — logout only deleted the cookie in that
# one browser (and in prod the delete even lacked the Secure flag, so
# some browsers kept the cookie), a password change never killed it,
# and anyone opening the site on a computer where someone else had
# logged in was silently signed in AS that person. There was also no
# way to revoke a session at all.
#
# Sessions are now DB rows (models.UserSession, Turso — so they still
# survive a server restart / Render sleep). The cookie holds only a
# random opaque token; only its SHA-256 is stored. Logout, password
# change, ban and admin reset REVOKE the row, and a replayed or
# leftover cookie is dead immediately. Old stateless cookies stop
# working on deploy — every user logs in once again, which is the
# fix itself, not a side effect.

import hashlib  # noqa: E402


def hash_session_token(token: str) -> str:
    """SHA-256 of a raw session token — what is stored and looked up."""
    return hashlib.sha256(token.encode()).hexdigest()


def _session_cookie_kwargs() -> dict:
    """Cookie flags — IDENTICAL for set and delete, or the delete does
    not clear the cookie. In prod the cookie is Secure; a delete sent
    without Secure cannot remove a Secure cookie in a modern browser,
    which is how "logged out" users stayed logged in."""
    return {
        "path": "/",
        "httponly": True,
        "samesite": "lax",
        "secure": get_settings().environment == "prod",
    }


def set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_MAX_AGE, **_session_cookie_kwargs()
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(SESSION_COOKIE, **_session_cookie_kwargs())


def _request_context(request: Request) -> tuple[str | None, str | None]:
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    return ip, (ua[:255] if ua else None)


def create_session(db, user: User, request: Request) -> str:
    """Create a session row and return the raw token for the cookie."""
    from datetime import datetime as _dt

    token = secrets.token_urlsafe(32)
    now = _dt.now(timezone.utc)
    ip, ua = _request_context(request)
    db.add(
        UserSession(
            token_hash=hash_session_token(token),
            user_id=user.id,
            created_at=now,
            expires_at=now + timedelta(seconds=SESSION_MAX_AGE),
            last_seen_at=now,
            ip_address=ip,
            user_agent=ua,
        )
    )
    db.commit()
    return token


def revoke_session(db, token: str) -> None:
    """Revoke one session by its raw token (no-op when unknown)."""
    row = db.query(UserSession).filter(
        UserSession.token_hash == hash_session_token(token)
    ).first()
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        db.commit()


def revoke_user_sessions(db, user_id: int, except_token: str | None = None) -> int:
    """Revoke every live session of a user, optionally keeping one
    (the current browser on a password change / "log out everywhere").
    Returns how many were revoked."""
    q = db.query(UserSession).filter(
        UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
    )
    if except_token:
        q = q.filter(UserSession.token_hash != hash_session_token(except_token))
    rows = q.all()
    now = datetime.now(timezone.utc)
    for row in rows:
        row.revoked_at = now
    if rows:
        db.commit()
    return len(rows)


def load_session_from_cookie(request: Request) -> "UserSession | None":
    """The live session row for this request's cookie (None when the
    cookie is missing, unknown, expired or revoked). Refreshes the
    activity stamp at most once per 5 minutes."""
    from app.models import UserSession as _UserSession

    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    db = SessionLocal()
    try:
        row = db.query(_UserSession).filter(
            _UserSession.token_hash == hash_session_token(token)
        ).first()
        if row is None or row.revoked_at is not None:
            return None
        now = datetime.now(timezone.utc)
        expires = row.expires_at
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires is not None and now > expires:
            return None
        last_seen = row.last_seen_at
        if last_seen is not None and last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        if last_seen is None or (now - last_seen) > timedelta(minutes=5):
            row.last_seen_at = now
            db.commit()
        return row
    finally:
        db.close()


def load_user_from_cookie(request: Request) -> User | None:
    """Resolve the signed-in user for a request (None if missing, invalid,
    expired, revoked, or banned). The user row is re-loaded from the DB
    on every request, so bans take effect immediately."""
    session = load_session_from_cookie(request)
    if session is None:
        return None
    db = SessionLocal()
    try:
        user = db.get(User, session.user_id)
        if user is None or user.is_banned:
            return None
        return user
    finally:
        db.close()


def get_current_session(request: Request) -> "UserSession | None":
    return getattr(request.state, "session", None)


# --- email verification + password-reset tokens (v3.5) ---------------------
#
# Signed with the same SECRET_KEY as sessions but a different salt, so a
# session cookie can never be replayed as an email token and vice versa.
# Verification links live 24h; reset links 1h AND are bound to the
# current password hash — once the password changes, every older reset
# link stops working, so a leaked link is single-use in practice.

_email_serializer = URLSafeTimedSerializer(settings.secret_key, salt="bytebatallion-email")
EMAIL_VERIFICATION_MAX_AGE = 24 * 60 * 60  # 24 hours
PASSWORD_RESET_MAX_AGE = 60 * 60  # 1 hour


def create_email_verification_token(user_id: int, email: str) -> str:
    return _email_serializer.dumps({"uid": user_id, "email": email, "kind": "verify"})


def read_email_verification_token(token: str) -> tuple[int, str] | None:
    try:
        data = _email_serializer.loads(token, max_age=EMAIL_VERIFICATION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or data.get("kind") != "verify":
        return None
    return int(data["uid"]), str(data["email"])


def _password_fingerprint(password_hash: str) -> str:
    """Short non-reversible fingerprint of the current password hash."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def create_password_reset_token(user: User) -> str:
    return _email_serializer.dumps({
        "uid": user.id, "email": user.email, "kind": "reset",
        "pwd": _password_fingerprint(user.password_hash),
    })


def read_password_reset_token(token: str, user: User) -> bool:
    """True only when the token matches this user AND the password has
    not changed since the token was issued."""
    try:
        data = _email_serializer.loads(token, max_age=PASSWORD_RESET_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return False
    if not isinstance(data, dict) or data.get("kind") != "reset":
        return False
    return (
        int(data.get("uid", -1)) == user.id
        and str(data.get("email", "")) == user.email
        and str(data.get("pwd", "")) == _password_fingerprint(user.password_hash)
    )


def read_password_reset_uid(token: str) -> int | None:
    """uid from a structurally valid reset token (signature, expiry and
    kind checked). The caller must still call
    read_password_reset_token(token, user) to confirm the token matches
    that user and the password is unchanged."""
    try:
        data = _email_serializer.loads(token, max_age=PASSWORD_RESET_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or data.get("kind") != "reset":
        return None
    return int(data["uid"])


# --- access-control dependencies ------------------------------------------

def _safe_next_value(value: str | None) -> str | None:
    """A `next` target is usable only when it is a relative path on this
    site: starts with `/` but NOT with `//` (no protocol-relative URLs,
    no absolute http(s) links — an open-redirect would leak sessions)."""
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return None


def _safe_next(request: Request) -> str | None:
    """The `next` query param, only when it is a safe relative path."""
    return _safe_next_value(request.query_params.get("next", ""))


def login_url(request: Request) -> str:
    """Login URL honouring a safe `next` query param, else plain login."""
    nxt = _safe_next(request)
    if nxt:
        return f"/auth/login?next={quote(nxt, safe='')}"
    return "/auth/login"


def login_redirect(request: Request) -> str:
    """Login URL that sends the user back to the page they asked for
    (current path + query string), used by the login guards."""
    path = request.url.path
    qs = request.url.query
    nxt = f"{path}?{qs}" if qs else path
    return f"/auth/login?next={quote(nxt, safe='')}"


def safe_next_or(request: Request, fallback: str, value: str | None = None) -> str:
    """Resolve a `next` target from an explicit value (e.g. a login form
    hidden field) or the request's query string; fall back when unsafe
    or absent."""
    nxt = _safe_next_value(value)
    if nxt is None:
        nxt = _safe_next(request)
    return nxt if nxt is not None else fallback


def get_current_user(request: Request) -> User | None:
    return getattr(request.state, "user", None)


def require_user(request: Request) -> User:
    """Dependency for login-required pages: redirects to /auth/login
    (with a `next` back to this page) instead of showing content."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=302, headers={"Location": login_redirect(request)})
    return user


def require_admin(request: Request) -> User:
    """Dependency for /admin routes: anonymous users are sent to login
    (with `next`); logged-in non-admins get 403."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=302, headers={"Location": login_redirect(request)})
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# --- CSRF (double-submit cookie) -------------------------------------------
#
# The token lives in a cookie AND as a hidden field in every form; POST
# handlers verify the two match. The middleware in main.py issues the
# token; templates render it via request.state.csrf_token.

async def verify_csrf(request: Request, csrf_token: str | None = Form(None)) -> None:
    cookie_token = request.cookies.get(CSRF_COOKIE)
    # A MISSING token and a WRONG token are both CSRF failures -> 403.
    # (Declared optional so a missing field reaches this check instead of
    # failing form validation first, which would yield a generic 400.)
    if not cookie_token or not csrf_token or not secrets.compare_digest(csrf_token, cookie_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


# --- login rate limiting ----------------------------------------------------

class LoginRateLimiter:
    """In-memory limiter: 5 failed tries -> 15-minute lockout.

    Keyed by (client IP, login name). Failures older than the lockout
    window are pruned automatically, so the lock expires on its own.
    (State resets on server restart — acceptable for v1 single-process.)
    """

    def __init__(self, max_failures: int = 5, lockout: timedelta = timedelta(minutes=15)):
        self.max_failures = max_failures
        self.lockout = lockout
        self._failures: dict[str, list[datetime]] = {}
        self._lock = threading.Lock()

    def _prune(self, failures: list[datetime], now: datetime) -> list[datetime]:
        return [t for t in failures if now - t < self.lockout]

    def is_locked(self, key: str) -> bool:
        with self._lock:
            now = datetime.now(timezone.utc)
            failures = self._prune(self._failures.get(key, []), now)
            self._failures[key] = failures
            return len(failures) >= self.max_failures

    def record_failure(self, key: str) -> None:
        with self._lock:
            now = datetime.now(timezone.utc)
            failures = self._prune(self._failures.get(key, []), now)
            failures.append(now)
            self._failures[key] = failures

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def reset_all(self) -> None:
        with self._lock:
            self._failures.clear()


login_rate_limiter = LoginRateLimiter()


# --- one-time admin password reveal (v0.1) -------------------------------------
#
# An admin resetting a user's password must see the new password ONCE.
# The old code put it in the redirect URL query string — query strings
# land in browser history and in server access logs (uvicorn logs the
# full request path), so the old "never stored or logged" comment was
# wrong. Now the password lives only in this in-memory, single-use
# store: the redirect carries a random token, the users page pops the
# password once, and a second load (or a 5-minute expiry) shows
# nothing. In-memory only — a restart drops pending reveals, which is
# safe: the admin just resets again.

class OneTimePasswordStore:
    """Random-token -> (username, password), single-use, 5-minute TTL."""

    def __init__(self, ttl: timedelta = timedelta(minutes=5)):
        self.ttl = ttl
        self._store: dict[str, tuple[str, str, datetime]] = {}
        self._lock = threading.Lock()

    def store(self, username: str, password: str) -> str:
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._store[token] = (username, password, datetime.now(timezone.utc))
        return token

    def take(self, token: str) -> tuple[str, str] | None:
        """Pop the password for a token (None when unknown or expired)."""
        with self._lock:
            entry = self._store.pop(token, None)
        if entry is None:
            return None
        username, password, created = entry
        if datetime.now(timezone.utc) - created > self.ttl:
            return None
        return username, password

    def reset_all(self) -> None:
        with self._lock:
            self._store.clear()


one_time_passwords = OneTimePasswordStore()


# --- short-lived instance download tokens (Phase 3) ---------------------------
#
# Start/Reset produce a download link that works for 5 minutes and is
# bound to ONE user + challenge, so a shared link is useless to anyone
# else (brief Phase 3, acceptance 5).

_download_serializer = URLSafeTimedSerializer(settings.secret_key, salt="bytebatallion-instance-download")
DOWNLOAD_TOKEN_MAX_AGE = 300  # 5 minutes


def create_download_token(user_id: int, challenge_id: int) -> str:
    return _download_serializer.dumps({"uid": user_id, "cid": challenge_id})


def read_download_token(token: str) -> tuple[int, int] | None:
    try:
        data = _download_serializer.loads(token, max_age=DOWNLOAD_TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or "uid" not in data or "cid" not in data:
        return None
    return int(data["uid"]), int(data["cid"])


# --- submission rate limiting (Phase 4) ----------------------------------------
#
# 1 submission per 5 seconds, max 20 per minute, per user (in-memory).

class SubmissionRateLimiter:
    def __init__(self, min_interval: timedelta = timedelta(seconds=5),
                 max_per_minute: int = 20):
        self.min_interval = min_interval
        self.max_per_minute = max_per_minute
        self._history: dict[int, list[datetime]] = {}
        self._lock = threading.Lock()

    def check(self, user_id: int) -> bool:
        """True when the submission may proceed."""
        with self._lock:
            now = datetime.now(timezone.utc)
            history = [
                t for t in self._history.get(user_id, [])
                if now - t <= timedelta(minutes=1)
            ]
            self._history[user_id] = history
            if history and now - history[-1] < self.min_interval:
                return False
            if len(history) >= self.max_per_minute:
                return False
            return True

    def record(self, user_id: int) -> None:
        with self._lock:
            self._history.setdefault(user_id, []).append(datetime.now(timezone.utc))

    def reset_all(self) -> None:
        with self._lock:
            self._history.clear()


submission_rate_limiter = SubmissionRateLimiter()
