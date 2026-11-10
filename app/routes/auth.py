"""Auth routes: register, login, logout (Phase 1).

Rules:
- passwords hashed with bcrypt — never stored or logged in plaintext
- registration always creates a `player`; only an admin can change roles
  (role management UI arrives in Phase 5)
- login is rate-limited: 5 failed tries -> 15-minute lockout
"""

import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import User, ctf_state
from app.security import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    create_session_token,
    hash_password,
    login_rate_limiter,
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
    user = User(username=username, email=email, password_hash=hash_password(password), role="player")
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
    return templates.TemplateResponse(
        request=request, name="auth/login.html",
        context={"error": error or None,
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
