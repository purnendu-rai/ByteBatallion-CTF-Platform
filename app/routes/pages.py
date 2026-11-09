"""Custom static pages (Phase 6): /pages/<slug>.

Published pages appear as nav links (loaded per request by the
middleware in main.py); drafts are admins-only.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Page

router = APIRouter(prefix="/pages", tags=["pages"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


@router.get("/{slug}")
def view_page(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Render one custom page by slug. Drafts are only visible to
    admins; everyone else gets a 404 (like an unpublished challenge)."""
    user = getattr(request.state, "user", None)
    page = db.query(Page).filter_by(slug=slug.lower()).first()
    is_admin = user is not None and user.role == "admin"
    if page is None or (not page.is_published and not is_admin):
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request, name="page.html", context={"page": page},
    )
