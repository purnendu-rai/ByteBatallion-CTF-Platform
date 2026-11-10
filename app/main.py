"""FastAPI application: routers, templates, static files, DB bootstrap."""

import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import get_settings
from app.create_admin import bootstrap_admin_from_env
from app.database import SessionLocal, get_db, init_db
from app.models import Page, load_public_config
from app.routes import admin, auth, challenges, notifications, pages, scoreboard, settings, submit, teams
from app.security import CSRF_COOKIE, SESSION_MAX_AGE, load_user_from_cookie

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Dev bootstrap: create all tables from the models on startup.
    init_db()
    # Hosts without shell access (Render free tier) create the first
    # admin from BOOTSTRAP_ADMIN_* env vars, when set.
    bootstrap_admin_from_env()
    yield


app = FastAPI(title="BYTE_BATALLION_CTF_PLATFORM - CTF Platform", lifespan=lifespan)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Routers are filled in phase by phase (brief, Section 4).
for router in (
    auth.router, challenges.router, submit.router, scoreboard.router,
    admin.router, teams.router, pages.router, settings.router,
    notifications.router,
):
    app.include_router(router)


@app.middleware("http")
async def session_and_csrf_middleware(request: Request, call_next):
    """Resolve the signed-in user (if any) and manage the CSRF token.

    - Session: the cookie holds only a SIGNED user id. The user row is
      re-loaded on every request, so bans take effect immediately and
      sessions survive a server restart.
    - CSRF: double-submit cookie. The random token lives in a cookie and
      every form echoes it back as a hidden field; POST handlers verify
      the two match (security.verify_csrf).
    """
    request.state.user = load_user_from_cookie(request)
    # Platform config snapshot (name, CTF phase) for the templates.
    request.state.cfg = load_public_config()

    # Phase 6: published custom pages feed the nav links on every page.
    db = SessionLocal()
    try:
        request.state.pages = (
            db.query(Page).filter(Page.is_published.is_(True))
            .order_by(Page.title).all()
        )
    finally:
        db.close()

    token = request.cookies.get(CSRF_COOKIE)
    if not token:
        token = secrets.token_hex(32)
    request.state.csrf_token = token

    response = await call_next(request)

    # Back-button freshness: a logged-in user hitting Back must see the
    # current state (e.g. a just-solved challenge already green), never a
    # stale cached page. Static assets and JSON APIs are left alone.
    if request.state.user and not request.url.path.startswith("/static"):
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            response.headers["Cache-Control"] = "no-store, no-cache"

    if request.cookies.get(CSRF_COOKIE) != token:
        response.set_cookie(
            CSRF_COOKIE, token,
            max_age=SESSION_MAX_AGE, httponly=True, samesite="lax",
            # v2.5: the session cookie was already Secure in prod; the
            # CSRF cookie was not — a network attacker on plain HTTP
            # could have read it. Same rule for both now.
            secure=get_settings().environment == "prod",
        )
    return response


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Security headers on every response (see SECURITY.md).

    - CSP: no inline scripts or inline event handlers anywhere in the
      app (the last two — the admin challenge form and the profile chart
      data — were moved to a static file / a JSON data block in v2.5),
      so script-src is a strict 'self'. Fonts come from Google Fonts,
      which is why style-src/font-src name those hosts. Inline style
      ATTRIBUTES (style="...") are still allowed via style-src-attr:
      templates and the scoreboard tooltip use them for colours, and a
      style attribute cannot execute script in a modern browser.
    - HSTS only in prod: on plain-http dev it would pin HTTPS and break
      local testing.
    """
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' https://fonts.googleapis.com; "
        "style-src-attr 'unsafe-inline'; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    )
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if get_settings().environment == "prod":
        response.headers["Strict-Transport-Security"] = (
            "max-age=63072000; includeSubDomains"
        )
    return response


@app.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    """Home page: CTF phase notice + the interactive terminal.

    Announcements live on the Notifications page now (v2) — they are no
    longer rendered here or on the board."""
    user = getattr(request.state, "user", None)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "cfg": request.state.cfg,
            "username": user.username if user else "guest",
        },
    )


# --- error pages (Phase 7): friendly, and leaking NOTHING ---------------------
#
# Unhandled errors in prod show a plain branded page — no stack traces, no
# file paths, no framework fingerprints. The real traceback goes to the
# server log only.

import logging  # noqa: E402

from app.config import get_settings  # noqa: E402

_logger = logging.getLogger("bytebatallion.error")


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    if get_settings().debug:
        raise exc  # in dev let the interactive traceback through
    _logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={"cfg": request.state.cfg, "status": 500},
        status_code=500,
    )


from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.responses import PlainTextResponse  # noqa: E402


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # A malformed request is the client's fault — reply plainly, never
    # echo internals of the request back.
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={"cfg": request.state.cfg, "status": 400},
        status_code=400,
    )


from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    """Branded pages for real 4xx/5xx errors — the body never includes
    internals. Sub-400 responses (e.g. the 302 login redirect raised by
    require_user) fall through unchanged, like Starlette's default."""
    if exc.status_code < 400:
        return PlainTextResponse(
            str(exc.detail) if exc.detail else "",
            status_code=exc.status_code, headers=exc.headers,
        )
    detail = {403: "You do not have access to this page.",
              404: "Page not found."}.get(exc.status_code, "Request failed.")
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={"cfg": request.state.cfg,
                 "status": exc.status_code,
                 "detail": detail},
        status_code=exc.status_code,
        headers=exc.headers,
    )
