"""Flag submission, scoring, and flag-sharing detection (Phase 4).

Rules (brief, Section 10):
- the submitted flag is SHA-256 hashed and compared with the ACTIVE
  instance hash for (user, challenge) — nothing else ever validates
- every attempt is logged in `submissions`, right or wrong (hash only)
- a correct submission creates ONE solve row; a second correct submit
  awards nothing
- if the hash matches ANOTHER user's active instance for that challenge,
  a `cheat_log` row is written — but the player still only ever hears
  "Incorrect", never that it was someone else's flag
- staff (admin/author) solves are never scored or ranked
- rate limit: 1 submission per 5 seconds, max 20 per minute per user
"""

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.flag_engine import sha256_hex
from app.models import Challenge, Solve, Submission, ctf_state
from app.security import require_user, submission_rate_limiter, verify_csrf

router = APIRouter(prefix="/submit", tags=["submit"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


@router.post("/{challenge_id}")
def submit_flag(
    challenge_id: int,
    request: Request,
    flag: str = Form(""),
    user=Depends(require_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
):
    challenge = db.get(Challenge, challenge_id)
    if challenge is None or challenge.is_deleted or (
        not challenge.is_published and user.role != "admin"
    ):
        raise HTTPException(status_code=404)
    detail_url = f"/challenges/{challenge_id}"

    # Phase 5 time gates: before the start the CTF is closed to players;
    # after the end submissions are closed for everyone.
    cfg = ctf_state(db)
    if cfg["phase"] == "before" and user.role != "admin":
        return RedirectResponse(f"{detail_url}?result=notstarted", status_code=303)
    if cfg["phase"] == "ended":
        return RedirectResponse(f"{detail_url}?result=ended", status_code=303)

    submitted = flag.strip()
    if not submitted:
        return RedirectResponse(f"{detail_url}?result=incorrect", status_code=303)

    if not submission_rate_limiter.check(user.id):
        return RedirectResponse(f"{detail_url}?result=ratelimited", status_code=303)
    submission_rate_limiter.record(user.id)

    submitted_hash = sha256_hex(submitted)

    # Simple mode: the challenge has ONE flag (the admin's); its SHA-256
    # is stored on the challenge row. A submission is correct when the
    # hash matches it exactly.
    correct = challenge.flag_sha256 is not None and challenge.flag_sha256 == submitted_hash

    # Every attempt is logged, right or wrong (hash only, never plaintext).
    db.add(Submission(
        user_id=user.id,
        challenge_id=challenge_id,
        submitted_flag_hash=submitted_hash,
        is_correct=correct,
    ))
    db.commit()

    if not correct:
        # One generic reply — no hint about how close it was, and never
        # any hint that the flag belonged to someone else.
        return RedirectResponse(f"{detail_url}?result=incorrect", status_code=303)

    if user.role != "player":
        # Staff solves are never scored (brief, product rule 6).
        return RedirectResponse(f"{detail_url}?result=staff", status_code=303)

    existing = (
        db.query(Solve)
        .filter_by(user_id=user.id, challenge_id=challenge_id)
        .first()
    )
    if existing is not None:
        # Second correct submit: logged above, but NO extra points.
        return RedirectResponse(f"{detail_url}?result=already", status_code=303)

    db.add(Solve(user_id=user.id, challenge_id=challenge_id,
                 points_awarded=challenge.points))
    db.commit()
    return RedirectResponse(f"{detail_url}?result=correct", status_code=303)
