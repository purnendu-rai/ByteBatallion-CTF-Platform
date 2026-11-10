"""Notifications (v2): announcements live here, not on the home page or
the challenge board.

- GET /notifications — the full list, newest first (login required).
- GET /notifications/unread.json — unread count + newest announcement
  for the nav red dot and the toast (login required).

Unread tracking needs NO database migration: the browser keeps the
highest announcement id it has seen in localStorage (`bb_seen_notif`);
visiting /notifications marks everything seen. See
app/static/js/notifications.js.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Announcement, User
from app.security import require_user

router = APIRouter(tags=["notifications"])
from app.timeutil import format_ist
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
templates.env.filters["ist"] = format_ist


@router.get("/notifications")
def notifications_page(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """All announcements, newest first. Visiting this page marks them
    read client-side (notifications.js sets `bb_seen_notif`)."""
    rows = (
        db.query(Announcement)
        .order_by(Announcement.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        request=request, name="notifications.html",
        context={"announcements": rows},
    )


@router.get("/notifications/unread.json")
def unread(
    request: Request,
    seen: str = "0",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """`?seen=<max announcement id the browser has seen>` (default 0,
    non-int tolerated). Returns the unread count, the newest
    announcement, and its id."""
    try:
        seen_id = int(seen)
    except (TypeError, ValueError):
        seen_id = 0
    latest = (
        db.query(Announcement)
        .order_by(Announcement.created_at.desc())
        .first()
    )
    unread_count = (
        db.query(func.count()).select_from(Announcement)
        .filter(Announcement.id > seen_id).scalar()
        or 0
    )
    latest_data = None
    latest_id = 0
    if latest is not None:
        latest_id = latest.id
        latest_data = {
            "id": latest.id,
            "title": latest.title,
            "body": latest.body,
            "created_at": latest.created_at.isoformat() if latest.created_at else None,
        }
    return JSONResponse(
        {"unread": unread_count, "latest": latest_data, "latest_id": latest_id}
    )
