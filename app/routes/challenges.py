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
    Challenge,
    HintUnlock,
    Solve,
    Submission,
    User,
    ctf_state,
    normalized_hints,
)
from app.security import login_redirect, require_user, verify_csrf

router = APIRouter(prefix="/challenges", tags=["challenges"])
from app.timeutil import format_ist, to_ist
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
templates.env.filters["ist"] = format_ist


@router.get("")
def board(request: Request, user=Depends(require_user), db: Session = Depends(get_db)):
    """Challenge board, grouped by category (published only).

    Order is upload order, not points or a fixed category list: the
    section whose FIRST challenge was uploaded earliest sits on top,
    and inside a section the oldest challenge is the leftmost card.
    Before the CTF starts, players see a countdown instead of the
    cards; after it ends a banner notes that submissions are closed
    (Phase 5)."""
    cfg = ctf_state(db)
    is_admin = user.role == "admin"
    show_cards = cfg["phase"] != "before" or is_admin
    groups: list[tuple[str, list[dict]]] = []
    first_bloods: dict[int, str] = {}
    solved_ids: set[int] = set()
    if show_cards:
        # Challenges this player has already solved — the board marks
        # their cards green with a [SOLVED] badge. A rejected solve
        # does not count as solved.
        solved_ids = {
            row.challenge_id
            for row in db.query(Solve.challenge_id)
            .filter(Solve.user_id == user.id, Solve.is_rejected.is_(False)).all()
        }
        # Solve counts count only solves that still score: a banned
        # player's solves and rejected solves are invisible here. The
        # ban/reject conditions sit INSIDE the joins (not a WHERE
        # filter) so a challenge whose only solves are banned/rejected
        # still shows — with 0 solves — instead of vanishing.
        rows = (
            db.query(Challenge, func.count(User.id))
            .outerjoin(Solve, (Solve.challenge_id == Challenge.id)
                       & (Solve.is_rejected.is_(False)))
            .outerjoin(User, (User.id == Solve.user_id)
                       & (User.is_banned.is_(False)))
            .filter(Challenge.is_published.is_(True), Challenge.is_deleted.is_(False))
            .order_by(Challenge.created_at, Challenge.id)
            .group_by(Challenge.id)
            .all()
        )
        by_category: dict[str, list[dict]] = {}
        for challenge, solve_count in rows:
            by_category.setdefault(challenge.category, []).append(
                {"challenge": challenge, "solves": solve_count}
            )
        # Sections in first-upload order: a category sits where its
        # earliest challenge sits (to_ist normalises naive timestamps
        # to UTC first, so the comparison never mixes naive/aware).
        groups = sorted(
            by_category.items(),
            key=lambda kv: (to_ist(kv[1][0]["challenge"].created_at),
                            kv[1][0]["challenge"].id),
        )
        # First blood: earliest solve per challenge, shown on the card (brief 10).
        # Banned players and rejected solves never hold first blood —
        # the earliest solve that still scores takes the badge.
        earliest = (
            db.query(Solve.challenge_id, func.min(Solve.solved_at).label("t"))
            .join(User, User.id == Solve.user_id)
            .filter(User.is_banned.is_(False), Solve.is_rejected.is_(False))
            .group_by(Solve.challenge_id)
            .subquery()
        )
        first_bloods = dict(
            db.query(Solve.challenge_id, User.username)
            .join(User, User.id == Solve.user_id)
            .join(earliest, (earliest.c.challenge_id == Solve.challenge_id)
                   & (earliest.c.t == Solve.solved_at))
            .filter(User.is_banned.is_(False), Solve.is_rejected.is_(False))
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
    # Solve count counts only solves that still score (banned players
    # and rejected solves are invisible — same rule as the board).
    solve_count = (
        db.query(func.count()).select_from(Solve)
        .join(User, User.id == Solve.user_id)
        .filter(Solve.challenge_id == challenge_id,
                User.is_banned.is_(False), Solve.is_rejected.is_(False))
        .scalar()
    )
    files = list_original_files(challenge_id)
    has_solved = (
        db.query(Solve).filter_by(user_id=user.id, challenge_id=challenge_id)
        .filter(Solve.is_rejected.is_(False))
        .first()
    ) is not None
    result = request.query_params.get("result", "")

    # Attempts (players only; staff are never locked out of previews).
    # A rejected solve locks the challenge outright; otherwise the
    # remaining count is max_attempts minus wrong submissions so far.
    rejected_solve = False
    attempts_remaining = None
    if user.role == "player":
        rejected_solve = (
            db.query(Solve).filter_by(user_id=user.id, challenge_id=challenge_id)
            .filter(Solve.is_rejected.is_(True))
            .first()
        ) is not None
        if challenge.max_attempts is not None and not has_solved:
            wrong = (
                db.query(func.count()).select_from(Submission)
                .filter(Submission.user_id == user.id,
                        Submission.challenge_id == challenge_id,
                        Submission.is_correct.is_(False))
                .scalar()
                or 0
            )
            attempts_remaining = max(0, challenge.max_attempts - wrong)
    attempts_locked = rejected_solve or (
        attempts_remaining is not None and attempts_remaining == 0
    )

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
            "attempts_remaining": attempts_remaining,
            "attempts_locked": attempts_locked,
            "rejected_solve": rejected_solve,
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
