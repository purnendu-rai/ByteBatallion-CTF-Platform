"""User settings routes: password change, account management."""

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.security import hash_password, require_user, verify_csrf, verify_password

router = APIRouter(tags=["settings"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


@router.get("/settings")
def settings_page(
    request: Request,
    user: User = Depends(require_user),
):
    """Render the user settings page. Success/error messages arrive via
    the PRG query string (?msg=... / ?error=...) after a password change."""
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "user": user,
            "error": request.query_params.get("error") or None,
            "success": request.query_params.get("msg") or None,
        },
    )


@router.post("/settings/password")
@router.post("/settings")
def change_password(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
):
    """Change the user's password after verifying the current password."""
    errors = []

    if not current_password:
        errors.append("Current password is required.")
    elif not verify_password(current_password, user.password_hash):
        errors.append("Current password is incorrect.")

    if len(new_password) < 8:
        errors.append("New password must be at least 8 characters long.")
    elif len(new_password) > 128:
        errors.append("New password is too long (maximum 128 characters).")

    if confirm_password and confirm_password != new_password:
        errors.append("New passwords do not match.")
    elif not confirm_password:
        errors.append("Please confirm your new password.")

    if errors:
        # PRG: the first error travels in the query string; the GET page
        # renders it.
        return RedirectResponse(
            "/settings?error=" + quote("; ".join(errors), safe=""),
            status_code=303,
        )

    # Re-fetch user in session to ensure persistence
    db_user = db.get(User, user.id)
    if db_user:
        db_user.password_hash = hash_password(new_password)
        db.commit()

    return RedirectResponse(
        "/settings?msg=" + quote("Your password has been updated successfully.", safe=""),
        status_code=303,
    )
