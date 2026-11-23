"""Auth routes: register, login, logout (Phase 1) + email verification
and password reset via Brevo (v3.5).

Rules:
- passwords hashed with bcrypt — never stored or logged in plaintext
- registration always creates a `player`; only an admin can change roles
  (role management UI arrives in Phase 5)
- login is rate-limited: 5 failed tries -> 15-minute lockout
- new accounts start unverified and cannot log in until they click the
  verification link (accounts created before v3.5 are grandfathered
  verified by the DB migration)
- verification is enforced ONLY when the mail service is configured
  (BREVO_API_KEY set): with no key, registration keeps its old
  behaviour (usable immediately) so a missing key can never brick
  signup — the live site always has the key set, so this only bites
  in dev/test, where the whole old test suite relies on it
- forgot-password / resend-verification never reveal whether an email
  is registered (same message either way)
"""

import re
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import email_service
from app.config import get_settings
from app.database import get_db
from app.models import User, ctf_state
from app.security import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    LoginRateLimiter,
    create_email_verification_token,
    create_password_reset_token,
    create_session_token,
    hash_password,
    login_rate_limiter,
    read_email_verification_token,
    read_password_reset_token,
    read_password_reset_uid,
    safe_next_or,
    verify_csrf,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])
from app.timeutil import format_ist
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
templates.env.filters["ist"] = format_ist

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,64}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Forgot-password / resend-verification requests are rate-limited too:
# 10 requests per (IP, email) in 15 minutes, so nobody can use the
# forms to spam someone's inbox.
email_request_limiter = LoginRateLimiter(max_failures=10, lockout=timedelta(minutes=15))


def _public_url() -> str:
    return get_settings().public_url.rstrip("/")


def _verification_enforced() -> bool:
    """Verification is enforced only when mail can actually go out."""
    return email_service.is_configured()


def _send_verification_email(user: User) -> bool:
    token = create_email_verification_token(user.id, user.email)
    link = f"{_public_url()}/auth/verify?token={token}"
    return email_service.send_verification_email(user.email, user.username, link)


def _send_password_reset_email(user: User) -> bool:
    token = create_password_reset_token(user)
    link = f"{_public_url()}/auth/reset-password?token={token}"
    return email_service.send_password_reset_email(user.email, user.username, link)


def _set_session_cookie(response: RedirectResponse, user_id: int) -> None:
    """Signed, HttpOnly, 7-day cookie. `Secure` is enabled in production."""
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(user_id),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=get_settings().environment == "prod",
    )


def _find_user(db: Session, login: str) -> User | None:
    """Login accepts username or email (case-insensitive)."""
    return (
        db.query(User).filter(func.lower(User.username) == login.lower()).first()
        or db.query(User).filter(func.lower(User.email) == login.lower()).first()
    )


def _validate_registration(db: Session, username: str, email: str, password: str) -> list[str]:
    errors: list[str] = []
    if not USERNAME_RE.fullmatch(username):
        errors.append("Username must be 3-64 characters (letters, digits, underscores).")
    if not EMAIL_RE.fullmatch(email):
        errors.append("Enter a valid email address.")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    if len(password) > 128:
        errors.append("Password is too long (max 128 characters).")
    if username and db.query(User.id).filter(func.lower(User.username) == username.lower()).first():
        errors.append("Username is already taken.")
    if email and db.query(User.id).filter(User.email == email).first():
        errors.append("Email is already registered.")
    return errors


@router.get("/register")
def register_page(request: Request):
    """GET renders any error carried over by the PRG redirect
    (?error=...&username=...&email=...)."""
    if request.state.user:
        return RedirectResponse("/challenges", status_code=303)
    error = request.query_params.get("error", "")
    return templates.TemplateResponse(
        request=request, name="auth/register.html",
        context={"errors": [error] if error else [],
                 "username": request.query_params.get("username", ""),
                 "email": request.query_params.get("email", ""),
                 "next": request.query_params.get("next", "")},
    )


@router.post("/register")
def register_submit(
    request: Request,
    username: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form(""),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
):
    username = username.strip()
    email = email.strip().lower()

    def _error_redirect(errors: list[str]) -> RedirectResponse:
        # PRG: the error travels in the query string; the GET page renders
        # it. The password is never echoed back. A safe `next` is kept so
        # a failed attempt does not lose the return target.
        nxt = safe_next_or(request, "", next)
        url = (
            "/auth/register?error=" + quote("; ".join(errors), safe="")
            + "&username=" + quote(username, safe="")
            + "&email=" + quote(email, safe="")
        )
        if nxt:
            url += "&next=" + quote(nxt, safe="")
        return RedirectResponse(url, status_code=303)

    # Phase 5: the admin config can close registration entirely.
    if not ctf_state(db)["registration_open"]:
        return _error_redirect(["Registration is closed."])

    errors = _validate_registration(db, username, email, password)
    if errors:
        return _error_redirect(errors)
    if _verification_enforced():
        user = User(username=username, email=email, password_hash=hash_password(password),
                    role="player", is_verified=False)
        db.add(user)
        db.commit()
        # New accounts must verify their email before they can log in.
        # If the send fails, the account still exists — the user can
        # resend the link later, and the failure is logged server-side
        # instead of breaking signup.
        if _send_verification_email(user):
            msg = "Account created — check your inbox for the verification link."
        else:
            msg = ("Account created, but the verification email could not be "
                   "sent right now. Use 'Resend verification email' on the "
                   "login page, or ask the admin.")
        url = "/auth/login?msg=" + quote(msg, safe="") + "&username=" + quote(username, safe="")
        return RedirectResponse(url, status_code=303)
    # Mail not configured: keep the pre-v3.5 behaviour (account usable
    # immediately) so signup never bricks on a missing API key.
    user = User(username=username, email=email, password_hash=hash_password(password),
                role="player", is_verified=True)
    db.add(user)
    db.commit()
    response = RedirectResponse(safe_next_or(request, "/challenges", next), status_code=303)
    _set_session_cookie(response, user.id)
    return response


@router.get("/login")
def login_page(request: Request):
    """GET renders any error carried over by the PRG redirect
    (?error=...&username=...)."""
    if request.state.user:
        return RedirectResponse("/challenges", status_code=303)
    error = request.query_params.get("error", "")
    msg = request.query_params.get("msg", "")
    return templates.TemplateResponse(
        request=request, name="auth/login.html",
        context={"error": error or None,
                 "msg": msg or None,
                 "username": request.query_params.get("username", ""),
                 "next": request.query_params.get("next", "")},
    )


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form(""),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
):
    login = username.strip()
    ip = request.client.host if request.client else "unknown"
    key = f"{ip}|{login.lower()}"

    def _error_redirect(message: str) -> RedirectResponse:
        # PRG: the error travels in the query string; the GET page renders
        # it. One generic message for bad credentials — never reveal
        # whether the account exists. A safe `next` is kept so a failed
        # attempt does not lose the return target.
        nxt = safe_next_or(request, "", next)
        url = (
            "/auth/login?error=" + quote(message, safe="")
            + "&username=" + quote(login, safe="")
        )
        if nxt:
            url += "&next=" + quote(nxt, safe="")
        return RedirectResponse(url, status_code=303)

    if login_rate_limiter.is_locked(key):
        return _error_redirect("Too many failed attempts. Try again in about 15 minutes.")

    user = _find_user(db, login) if login else None
    if user is not None and verify_password(password, user.password_hash):
        if user.is_banned:
            return _error_redirect("Your account has been banned.")
        if not user.is_verified and _verification_enforced():
            return _error_redirect(
                "Please verify your email first — check your inbox for the "
                "verification link, or use 'Resend verification email' below.")
        login_rate_limiter.reset(key)
        response = RedirectResponse(safe_next_or(request, "/challenges", next), status_code=303)
        _set_session_cookie(response, user.id)
        return response

    login_rate_limiter.record_failure(key)
    return _error_redirect("Incorrect username or password.")


@router.post("/logout")
def logout(_: None = Depends(verify_csrf)):
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


# --- email verification (v3.5) ----------------------------------------------

@router.get("/verify")
def verify_email(request: Request, token: str = "", db: Session = Depends(get_db)):
    """Clicking the link in the verification mail lands here."""
    def _login_redirect(msg: str) -> RedirectResponse:
        return RedirectResponse("/auth/login?msg=" + quote(msg, safe=""), status_code=303)

    data = read_email_verification_token(token) if token else None
    if data is None:
        return RedirectResponse(
            "/auth/resend-verification?error=" + quote(
                "That verification link is invalid or has expired. "
                "Request a new one below.", safe=""),
            status_code=303)
    user_id, email = data
    user = db.get(User, user_id)
    if user is None or user.email != email:
        return RedirectResponse(
            "/auth/resend-verification?error=" + quote(
                "That verification link does not match any account. "
                "Request a new one below.", safe=""),
            status_code=303)
    if user.is_verified:
        return _login_redirect("Your email is already verified — log in.")
    user.is_verified = True
    db.commit()
    response = RedirectResponse("/challenges", status_code=303)
    _set_session_cookie(response, user.id)
    return response


@router.get("/resend-verification")
def resend_verification_page(request: Request):
    if request.state.user:
        return RedirectResponse("/challenges", status_code=303)
    error = request.query_params.get("error", "")
    msg = request.query_params.get("msg", "")
    return templates.TemplateResponse(
        request=request, name="auth/resend_verification.html",
        context={"error": error or None, "msg": msg or None,
                 "email": request.query_params.get("email", "")},
    )


@router.post("/resend-verification")
def resend_verification_submit(
    request: Request,
    email: str = Form(""),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
):
    email = email.strip().lower()
    ip = request.client.host if request.client else "unknown"
    key = f"{ip}|{email}"
    # Same message whether the account exists or not — never reveal it.
    done_url = ("/auth/resend-verification?msg=" + quote(
        "If an unverified account exists for that email, a new "
        "verification link has been sent.", safe="")
        + "&email=" + quote(email, safe=""))
    if email and not email_request_limiter.is_locked(key):
        user = db.query(User).filter(func.lower(User.email) == email).first()
        if user is not None and not user.is_verified:
            _send_verification_email(user)
            email_request_limiter.record_failure(key)
    return RedirectResponse(done_url, status_code=303)


# --- forgot / reset password (v3.5) ------------------------------------------

@router.get("/forgot-password")
def forgot_password_page(request: Request):
    if request.state.user:
        return RedirectResponse("/challenges", status_code=303)
    msg = request.query_params.get("msg", "")
    return templates.TemplateResponse(
        request=request, name="auth/forgot_password.html",
        context={"msg": msg or None,
                 "email": request.query_params.get("email", "")},
    )


@router.post("/forgot-password")
def forgot_password_submit(
    request: Request,
    email: str = Form(""),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
):
    email = email.strip().lower()
    ip = request.client.host if request.client else "unknown"
    key = f"{ip}|{email}"
    # Same message whether the account exists or not — never reveal it.
    done_url = ("/auth/forgot-password?msg=" + quote(
        "If an account exists for that email, a password reset link has "
        "been sent (valid for 1 hour).", safe="")
        + "&email=" + quote(email, safe=""))
    if email and not email_request_limiter.is_locked(key):
        user = db.query(User).filter(func.lower(User.email) == email).first()
        if user is not None:
            _send_password_reset_email(user)
            email_request_limiter.record_failure(key)
    return RedirectResponse(done_url, status_code=303)


@router.get("/reset-password")
def reset_password_page(request: Request, token: str = "", db: Session = Depends(get_db)):
    """The reset link lands here; an invalid/expired token goes to the
    forgot-password page instead of showing the form."""
    if request.state.user:
        return RedirectResponse("/challenges", status_code=303)
    user = _user_for_reset_token(token, db) if token else None
    if user is None:
        return RedirectResponse(
            "/auth/forgot-password?msg=" + quote(
                "That reset link is invalid or has expired. Request a new "
                "one below.", safe=""),
            status_code=303)
    error = request.query_params.get("error", "")
    return templates.TemplateResponse(
        request=request, name="auth/reset_password.html",
        context={"error": error or None, "token": token,
                 "username": user.username},
    )


@router.post("/reset-password")
def reset_password_submit(
    request: Request,
    token: str = Form(""),
    password: str = Form(""),
    password_confirm: str = Form(""),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
):
    def _error_redirect(message: str) -> RedirectResponse:
        return RedirectResponse(
            "/auth/reset-password?token=" + quote(token, safe="")
            + "&error=" + quote(message, safe=""),
            status_code=303)

    user = _user_for_reset_token(token, db) if token else None
    if user is None:
        return RedirectResponse(
            "/auth/forgot-password?msg=" + quote(
                "That reset link is invalid or has expired. Request a new "
                "one below.", safe=""),
            status_code=303)
    if len(password) < 8 or len(password) > 128:
        return _error_redirect("Password must be 8-128 characters.")
    if password != password_confirm:
        return _error_redirect("The two passwords do not match.")
    user.password_hash = hash_password(password)
    db.commit()
    return RedirectResponse(
        "/auth/login?msg=" + quote(
            "Password updated — log in with your new password.", safe="")
        + "&username=" + quote(user.username, safe=""),
        status_code=303)


def _user_for_reset_token(token: str, db: Session) -> User | None:
    """Resolve the user a reset token belongs to (None when invalid,
    expired, or issued before the last password change)."""
    uid = read_password_reset_uid(token)
    if uid is None:
        return None
    user = db.get(User, uid)
    if user is None or not read_password_reset_token(token, user):
        return None
    return user
