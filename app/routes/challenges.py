"""Challenge board, detail, and per-user flag instances (Phases 2 + 3).

Player file access is ALWAYS through their personal instance zip
(Phase 3): the platform copies the originals, replaces the exact string
FLAG_PLACEHOLDER with the player's own flag, re-zips, and serves it via
a short-lived, user-bound token link. Originals are never web-served —
the raw-file route below is ADMIN-ONLY (for verifying uploads).
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.flag_engine import list_original_files
from app.storage import get_storage
from app.models import (
    CHALLENGE_CATEGORIES,
    Challenge,
    HintUnlock,
    Solve,
    User,
    ctf_state,
    normalized_hints,
)
from app.security import login_redirect, require_user, verify_csrf

router = APIRouter(prefix="/challenges", tags=["challenges"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


@router.get("")
def board(request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    """Challenge board, grouped by category (published only).

    Before the CTF starts, players see a countdown instead of the cards;
    after it ends a banner notes that submissions are closed (Phase 5)."""
    cfg = ctf_state(db)
    is_admin = user.role == "admin"
    show_cards = cfg["phase"] != "before" or is_admin
    groups: list[tuple[str, list[dict]]] = []
    first_bloods: dict[int, str] = {}
    solved_ids: set[int] = set()
    if show_cards:
        # Challenges this player has already solved — the board marks
        # their cards green with a [SOLVED] badge.
        solved_ids = {
            row.challenge_id
            for row in db.query(Solve.challenge_id)
            .filter(Solve.user_id == user.id).all()
        }
        rows = (
            db.query(Challenge, func.count(Solve.user_id))
            .outerjoin(Solve, Solve.challenge_id == Challenge.id)
            .filter(Challenge.is_published.is_(True), Challenge.is_deleted.is_(False))
            .order_by(Challenge.points)
            .group_by(Challenge.id)
            .all()
        )
        by_category: dict[str, list[dict]] = {}
        for challenge, solve_count in rows:
            by_category.setdefault(challenge.category, []).append(
                {"challenge": challenge, "solves": solve_count}
            )
        groups = sorted(
            by_category.items(),
            key=lambda kv: (CHALLENGE_CATEGORIES.index(kv[0]) if kv[0] in CHALLENGE_CATEGORIES else 99, kv[0]),
        )
        # First blood: earliest solve per challenge, shown on the card (brief 10).
        earliest = (
            db.query(Solve.challenge_id, func.min(Solve.solved_at).label("t"))
            .group_by(Solve.challenge_id)
            .subquery()
        )
        first_bloods = dict(
            db.query(Solve.challenge_id, User.username)
            .join(User, User.id == Solve.user_id)
            .join(earliest, (earliest.c.challenge_id == Solve.challenge_id)
                   & (earliest.c.t == Solve.solved_at))
            .all()
        )
    return templates.TemplateResponse(
        request=request, name="challenges/board.html",
        context={"groups": groups, "first_bloods": first_bloods,
                 "cfg": cfg, "solved_ids": solved_ids, "show_cards": show_cards})


@router.get("/{challenge_id}")
def detail(
    challenge_id: int,
    request: Request,
    user=Depends(require_user),
    db: Session = Depends(get_db),
):
    """Challenge detail + downloadable files. The admin sets ONE flag per
    challenge; the player reads the files and submits that flag."""
    challenge = db.get(Challenge, challenge_id)
    if challenge is None or challenge.is_deleted:
        raise HTTPException(status_code=404)
    if not challenge.is_published and user.role != "admin":
        raise HTTPException(status_code=404)
    # Before the CTF starts, players are redirected to the countdown board.
    if ctf_state(db)["phase"] == "before" and user.role != "admin":
        return RedirectResponse("/challenges", status_code=303)
    solve_count = (
        db.query(func.count()).select_from(Solve)
        .filter(Solve.challenge_id == challenge_id).scalar()
    )
    files = list_original_files(challenge_id)
    has_solved = (
        db.query(Solve).filter_by(user_id=user.id, challenge_id=challenge_id).first()
    ) is not None
    result = request.query_params.get("result", "")

    # Phase 6 — hints with costs. The template needs, per hint, whether
    # the player already unlocked it (paid hints are shown afterwards
    # with no further charge). Staff/admin previews cost nothing.
    all_hints = normalized_hints(challenge.hints)
    unlocked_indexes: set[int] = set()
    if all_hints:
        unlocked_indexes = {
            row.hint_index
            for row in db.query(HintUnlock.hint_index).filter_by(
                user_id=user.id, challenge_id=challenge_id
            ).all()
        }
    hints = [
        {**h, "unlocked": h["index"] in unlocked_indexes}
        for h in all_hints
    ]
    return templates.TemplateResponse(
        request=request,
        name="challenges/detail.html",
        context={
            "challenge": challenge,
            "solve_count": solve_count,
            "hints": hints,
            "is_admin": user.role == "admin",
            "files": files,
            "has_files": bool(files),
            "has_solved": has_solved,
            "result": result,
        },
    )


def _get_challenge_or_404(db: Session, challenge_id: int, user) -> Challenge:
    challenge = db.get(Challenge, challenge_id)
    if challenge is None or challenge.is_deleted or (
        not challenge.is_published and user.role != "admin"
    ):
        raise HTTPException(status_code=404)
    return challenge


@router.post("/{challenge_id}/hints/{hint_index}/view")
def view_hint(
    challenge_id: int,
    hint_index: int,
    request: Request,
    user=Depends(require_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
):
    """Unlock ONE hint for the player (Phase 6).

    - Free hints: reveal immediately, no charge.
    - Paid hints: charged exactly ONCE (the first view creates the
      HintUnlock row); later views are free.
    - The charge is capped at the player's current total so a score can
      never go below 0 (brief §12).
    - Staff/admin never get charged for previewing a hint.
    """
    challenge = _get_challenge_or_404(db, challenge_id, user)
    hints = normalized_hints(challenge.hints)
    hint = next((h for h in hints if h["index"] == hint_index), None)
    if hint is None or (not challenge.is_published and user.role != "admin"):
        raise HTTPException(status_code=404)

    unlock = (
        db.query(HintUnlock)
        .filter_by(user_id=user.id, challenge_id=challenge.id,
                   hint_index=hint_index)
        .first()
    )
    if unlock is None:
        if hint["cost"] > 0 and user.role == "player":
            # Deduction, floored: charge at most what the player has,
            # so their solving total can never drop below 0.
            current_points = (
                db.query(func.coalesce(func.sum(Solve.points_awarded), 0))
                .filter(Solve.user_id == user.id)
                .scalar()
            )
            charge = min(hint["cost"], int(current_points))
        else:
            charge = 0  # free hint, or a staff preview
        db.add(HintUnlock(
            user_id=user.id, challenge_id=challenge.id,
            hint_index=hint_index, points_charged=charge,
        ))
        db.commit()
    return RedirectResponse(f"/challenges/{challenge_id}#hints", status_code=303)


def perform_reset(db: Session, challenge: Challenge, user_id: int) -> None:
    """Deprecated: retained as a no-op stub for backward compatibility.

    The per-user instance/flag-injection engine was removed in favour of
    the simple single-flag model (the admin sets ONE flag per challenge).
    Nothing calls this any more."""
    return None


@router.get("/{challenge_id}/download/{filename}")
def download_challenge_file(
    challenge_id: int,
    filename: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Download an author-uploaded challenge file (login required).

    Files are served ONLY to signed-in users (anonymous users are sent
    to login, with a `next` back here). The active storage backend
    serves them: local disk (FileResponse) or Backblaze B2
    (streamed from the private bucket, never a public URL).
    """
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=302, headers={"Location": login_redirect(request)})
    challenge = _get_challenge_or_404(db, challenge_id, user)
    return get_storage().download_response(challenge.id, filename)


@router.get("/{challenge_id}/files/{filename}")
def download_original(
    challenge_id: int,
    filename: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Legacy admin-style path; kept as an alias to the login-gated
    downloader so old links keep working."""
    return download_challenge_file(challenge_id, filename, request, db)
