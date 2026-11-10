"""Admin portal — full admin access (Phase 5), admin role only.

Everything from the brief, Section 11: dashboard, challenge CRUD +
bulk import, user management, submissions + cheat log, announcements,
config (with CTF time gates), and export. Every route is guarded by
require_admin.
"""

import csv
import io
import json
import re
import secrets
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape as _xml_escape

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Integer, func, or_
from sqlalchemy.orm import Session, aliased

from app.database import get_db
from app.flag_engine import (
    delete_uploaded_file,
    list_original_files,
    save_uploaded_files,
    sha256_hex,
    validate_challenge_flag,
)
from app.models import (
    CHALLENGE_CATEGORIES,
    Announcement,
    Challenge,
    CheatLog,
    HintUnlock,
    Instance,
    Page,
    Solve,
    Submission,
    Team,
    TeamMembership,
    User,
    ctf_state,
    normalized_hints,
    set_config,
    utc_now,
)
from app.routes.scoreboard import ranked_players
from app.security import hash_password, one_time_passwords, require_admin, verify_csrf

router = APIRouter(prefix="/admin", tags=["admin"])
from app.timeutil import IST, format_ist, to_ist
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
templates.env.filters["ist"] = format_ist

VALID_ROLES = ("player", "author", "admin")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
IMPORT_MAX_ITEMS = 500
PAGE_MAX_SLUG = 64


def _generate_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


# --- dashboard (Phase 5) -----------------------------------------------------

@router.get("")
def dashboard(
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    stats = {
        "users": db.query(func.count()).select_from(User).scalar(),
        "challenges": db.query(func.count()).select_from(Challenge)
            .filter(Challenge.is_deleted.is_(False)).scalar(),
        "solves": db.query(func.count()).select_from(Solve).scalar(),
        "submissions": db.query(func.count()).select_from(Submission).scalar(),
    }
    # Solves per day for the last 14 days (portable range filter, no
    # SQL-dialect date functions). Days are IST days, like every other
    # time the site shows.
    today = datetime.now(IST).date()
    day_counts = []
    for i in range(13, -1, -1):
        day = today - timedelta(days=i)
        day_start = datetime(day.year, day.month, day.day, tzinfo=IST)
        count = (
            db.query(func.count()).select_from(Solve)
            .filter(Solve.solved_at >= day_start,
                    Solve.solved_at < day_start + timedelta(days=1))
            .scalar()
        )
        day_counts.append({"label": day.strftime("%d %b"), "count": count})
    max_count = max((d["count"] for d in day_counts), default=0) or 1
    for d in day_counts:
        d["pct"] = round(d["count"] * 100 / max_count)

    recent = (
        db.query(Submission, User.username, Challenge.title)
        .join(User, User.id == Submission.user_id)
        .join(Challenge, Challenge.id == Submission.challenge_id)
        .order_by(Submission.submitted_at.desc())
        .limit(15)
        .all()
    )
    return templates.TemplateResponse(
        request=request, name="admin/dashboard.html",
        context={"stats": stats, "days": day_counts, "recent": recent},
    )


# --- challenge CRUD (Phase 2) + bulk import (Phase 5) -------------------------

def _form_from_challenge(ch: Challenge) -> dict:
    return {
        "title": ch.title,
        "category": ch.category,
        "description": ch.description,
        "points": str(ch.points),
        # Attempts dropdown: "" renders as Unlimited.
        "max_attempts": "" if ch.max_attempts is None else str(ch.max_attempts),
        # One hint per line; a hint with a cost ends with "| <points>".
        "hints": "\n".join(
            f"{h['text']} | {h['cost']}" if h["cost"] else h["text"]
            for h in normalized_hints(ch.hints)
        ),
        # The flag is never shown back (only its hash is stored).
        "flag": "",
    }


def _parse_hint_line(line: str, errors: list[str]) -> dict | None:
    """Parse one hint line: `text` (free) or `text | cost` (points).
    Legacy plain lines (no cost, or with "| 0" cost) stay free."""
    text, sep, cost_raw = line.rpartition("|")
    if not sep:
        return {"text": line.strip(), "cost": 0}
    text = text.strip()
    cost_raw = cost_raw.strip()
    if not text:
        errors.append("Hint text is required (a hint cannot be empty).")
        return None
    try:
        cost = int(cost_raw) if cost_raw else 0
    except ValueError:
        # Not a number after the last "|" — the "|" is part of the text.
        return {"text": line.strip(), "cost": 0}
    if cost < 0 or cost > 100000:
        errors.append(f"Hint cost must be between 0 and 100000 ('{text[:40]}').")
        return None
    return {"text": text, "cost": cost}


def _parse_challenge_form(form: dict, existing_sha: str | None = None) -> tuple[list[str], dict]:
    """Validate the challenge form; returns (errors, clean_data).

    The admin types ONE concrete flag for the challenge. We store only its
    SHA-256 hash (`flag_sha256`); the plaintext is discarded here and never
    logged. On EDIT, leaving the flag field blank keeps the existing flag.
    """
    errors: list[str] = []
    title = (form.get("title") or "").strip()
    category = (form.get("category") or "").strip()
    # "__custom__" in the dropdown means: use the typed custom category.
    if category == "__custom__":
        category = (form.get("custom_category") or "").strip()
    description = (form.get("description") or "").strip()
    points_raw = (form.get("points") or "").strip() or "100"
    flag = (form.get("flag") or "").strip()
    # Attempts: "" = Unlimited (None); otherwise an integer 1–20.
    attempts_raw = (form.get("max_attempts") or "").strip()
    if not attempts_raw:
        max_attempts = None
    else:
        try:
            max_attempts = int(attempts_raw)
            if not 1 <= max_attempts <= 20:
                errors.append("Attempts must be Unlimited or between 1 and 20.")
                max_attempts = None
        except ValueError:
            errors.append("Attempts must be Unlimited or between 1 and 20.")
            max_attempts = None

    # Hints (Phase 6): one per line, optional `| cost` suffix. Plain
    # lines are free hints (cost 0); legacy strings stay readable.
    hints: list[dict] = []
    for line in (form.get("hints") or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parsed = _parse_hint_line(line, errors)
        if parsed is not None:
            hints.append(parsed)

    if not title:
        errors.append("Title is required.")
    elif len(title) > 200:
        errors.append("Title is too long (max 200 characters).")
    # Category: pick from the list OR type a custom one. Normalise to a
    # short lowercase label so grouping stays clean; any non-empty value
    # is allowed (no more fixed-list-only restriction).
    category = re.sub(r"\s+", "-", category.strip().lower())[:32]
    if not category:
        errors.append("Category is required (pick one or type a custom category).")
    try:
        points = int(points_raw)
        if not 0 <= points <= 100000:
            errors.append("Points must be between 0 and 100000.")
    except ValueError:
        points = 100
        errors.append("Points must be a whole number.")

    # Flag: required on create; optional on edit (blank = keep existing).
    if flag:
        ok, err = validate_challenge_flag(flag)
        if not ok:
            errors.append(f"Flag: {err}")
        flag_sha256 = sha256_hex(flag) if ok else existing_sha
    elif existing_sha:
        flag_sha256 = existing_sha  # edit kept the current flag
    else:
        flag_sha256 = None
        errors.append("Flag is required.")

    clean = {
        "title": title,
        "category": category,
        "description": description,
        "points": points,
        "max_attempts": max_attempts,
        "hints": hints,
        "flag_template": "",          # plaintext flag is never stored
        "flag_sha256": flag_sha256,
    }
    return errors, clean


def _get_challenge_or_404(db: Session, challenge_id: int) -> Challenge:
    challenge = db.get(Challenge, challenge_id)
    if challenge is None:
        raise HTTPException(status_code=404)
    return challenge


@router.get("/challenges")
def challenges_list(
    request: Request,
    msg: str = "",
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    solve_counts = dict(
        db.query(Solve.challenge_id, func.count()).group_by(Solve.challenge_id).all()
    )
    challenges = db.query(Challenge).order_by(Challenge.id).all()
    return templates.TemplateResponse(
        request=request,
        name="admin/challenges_list.html",
        context={"challenges": challenges, "solve_counts": solve_counts, "msg": msg},
    )


@router.get("/challenges/new")
def challenge_new_form(request: Request, _admin: User = Depends(require_admin)):
    """An `error` query param (from the PRG redirect after a failed save)
    is rendered the same way as inline form errors."""
    error = request.query_params.get("error", "")
    return templates.TemplateResponse(
        request=request,
        name="admin/challenge_form.html",
        context={
            "form": {}, "errors": [error] if error else [], "challenge": None, "files": [],
            "has_flag": False,
            "categories": CHALLENGE_CATEGORIES, "action_url": "/admin/challenges/new",
            "file_count": 1,
        },
    )


@router.post("/challenges/new")
async def challenge_create(
    request: Request,
    title: str = Form(""),
    category: str = Form(""),
    custom_category: str = Form(""),
    description: str = Form(""),
    points: str = Form("100"),
    max_attempts: str = Form(""),
    hints: str = Form(""),
    flag: str = Form(""),
    files: list[UploadFile] | None = None,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    _: None = Depends(verify_csrf),
):
    form = {"title": title, "category": category, "custom_category": custom_category,
            "description": description, "points": points, "max_attempts": max_attempts,
            "hints": hints, "flag": flag}
    errors, clean = _parse_challenge_form(form)
    if errors:
        # PRG: the error travels in the query string. Field values are
        # lost on the redirect — the admin re-types them (noted in
        # UI-CHANGES.md).
        return RedirectResponse(
            "/admin/challenges/new?error=" + quote("; ".join(errors), safe=""),
            status_code=303,
        )
    # New challenges always start as drafts (brief: visible after publishing).
    challenge = Challenge(author_id=admin.id, is_published=False, **clean)
    db.add(challenge)
    db.commit()
    await save_uploaded_files(challenge.id, files)
    return RedirectResponse(f"/admin/challenges/{challenge.id}/edit", status_code=303)


@router.get("/challenges/{challenge_id}/edit")
def challenge_edit_form(
    challenge_id: int,
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    challenge = _get_challenge_or_404(db, challenge_id)
    error = request.query_params.get("error", "")
    return templates.TemplateResponse(
        request=request,
        name="admin/challenge_form.html",
        context={
            "form": _form_from_challenge(challenge),
            "errors": [error] if error else [], "challenge": challenge,
            "files": list_original_files(challenge.id),
            "has_flag": bool(challenge.flag_sha256),
            "categories": CHALLENGE_CATEGORIES,
            "action_url": f"/admin/challenges/{challenge.id}/edit",
            "file_count": max(1, len(list_original_files(challenge.id))),
        },
    )


@router.post("/challenges/{challenge_id}/edit")
async def challenge_edit(
    challenge_id: int,
    request: Request,
    title: str = Form(""),
    category: str = Form(""),
    custom_category: str = Form(""),
    description: str = Form(""),
    points: str = Form("100"),
    max_attempts: str = Form(""),
    hints: str = Form(""),
    flag: str = Form(""),
    files: list[UploadFile] | None = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    _: None = Depends(verify_csrf),
):
    challenge = _get_challenge_or_404(db, challenge_id)
    form = {"title": title, "category": category, "custom_category": custom_category,
            "description": description, "points": points, "max_attempts": max_attempts,
            "hints": hints, "flag": flag}
    errors, clean = _parse_challenge_form(form, existing_sha=challenge.flag_sha256)
    if errors:
        # PRG: the error travels in the query string (field values are
        # lost on the redirect — see UI-CHANGES.md).
        return RedirectResponse(
            f"/admin/challenges/{challenge.id}/edit?error="
            + quote("; ".join(errors), safe=""),
            status_code=303,
        )
    for key, value in clean.items():
        setattr(challenge, key, value)
    db.commit()
    await save_uploaded_files(challenge.id, files)
    return RedirectResponse(f"/admin/challenges/{challenge.id}/edit", status_code=303)


@router.post("/challenges/{challenge_id}/publish")
def challenge_publish(
    challenge_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    _: None = Depends(verify_csrf),
):
    challenge = _get_challenge_or_404(db, challenge_id)
    if not challenge.is_deleted:
        challenge.is_published = not challenge.is_published
        db.commit()
    return RedirectResponse("/admin/challenges", status_code=303)


@router.post("/challenges/{challenge_id}/delete")
def challenge_delete(
    challenge_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    _: None = Depends(verify_csrf),
):
    challenge = _get_challenge_or_404(db, challenge_id)
    # Soft delete: the row (and its solves) stays in the DB.
    challenge.is_deleted = True
    challenge.is_published = False
    db.commit()
    return RedirectResponse("/admin/challenges", status_code=303)


@router.post("/challenges/{challenge_id}/files/{filename}/delete")
def challenge_file_delete(
    challenge_id: int,
    filename: str,
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    _: None = Depends(verify_csrf),
):
    """Delete ONE already-uploaded file from the edit form.

    The name must be exactly its sanitized form (anything with a path
    or odd characters 404s) — the edit page only ever links names from
    `list_original_files`. Deleting the original means future
    downloads of it 404; already-downloaded copies are unaffected.
    """
    from app.flag_engine import sanitize_filename

    challenge = _get_challenge_or_404(db, challenge_id)
    if sanitize_filename(filename) != filename or filename not in list_original_files(challenge.id):
        raise HTTPException(status_code=404)
    delete_uploaded_file(challenge.id, filename)
    return RedirectResponse(f"/admin/challenges/{challenge.id}/edit", status_code=303)


@router.post("/challenges/publish")
def challenges_bulk_publish(
    request: Request,
    challenge_ids: list[int] = Form([]),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    _: None = Depends(verify_csrf),
):
    """Publish several drafts TOGETHER.

    Every selected challenge gets the SAME `created_at` timestamp, so
    on the board (which orders by first-upload time) they appear as
    one group at the same moment instead of staggered. Already
    published or deleted challenges are left exactly where they are.
    """
    now = utc_now()
    published = 0
    for challenge_id in dict.fromkeys(challenge_ids):
        challenge = db.get(Challenge, challenge_id)
        if challenge is None or challenge.is_deleted or challenge.is_published:
            continue
        challenge.is_published = True
        challenge.created_at = now
        published += 1
    db.commit()
    return RedirectResponse(
        f"/admin/challenges?msg=Published {published} challenge(s) together.",
        status_code=303,
    )


# --- bulk import (Phase 5, brief 9.1) -----------------------------------------

def _convert_import_entry(label: str, title: str, category: str, points_raw: str,
                          flag: str, description: str, items: list[dict],
                          errors: list[str]) -> None:
    """Validate one imported challenge; the flag is kept as a plaintext
    hash so the challenge can be solved by matching that exact flag."""
    title = title.strip()
    if not title:
        errors.append(f"{label}: title is required.")
        return
    category = re.sub(r"\s+", "-", (category or "misc").strip().lower())[:32] or "misc"
    try:
        points = int(str(points_raw).strip())
    except ValueError:
        points = 100
    flag = flag.strip()
    ok, err = validate_challenge_flag(flag)
    if not ok:
        errors.append(f"{label} '{title}': {err}")
        return
    items.append({
        "title": title,
        "category": category,
        "points": points,
        "description": description.strip(),
        "flag_sha256": sha256_hex(flag),
    })


def _parse_import(payload: str, fmt: str) -> tuple[list[dict], list[str]]:
    items: list[dict] = []
    errors: list[str] = []
    payload = (payload or "").strip()
    if not payload:
        return [], ["Nothing to import — paste JSON or CSV first."]
    if fmt == "csv":
        rows = [r for r in csv.reader(io.StringIO(payload)) if r and any(c.strip() for c in r)]
        if rows and rows[0][0].strip().lower() == "title":
            rows = rows[1:]
        for i, row in enumerate(rows, 2):
            if len(row) < 4:
                errors.append(f"CSV line {i}: expected title,category,points,flag")
                continue
            _convert_import_entry(f"CSV line {i}", row[0], row[1], row[2], row[3], "", items, errors)
    else:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as e:
            return [], [f"Invalid JSON: {e}"]
        if not isinstance(data, list):
            return [], ["JSON must be an array of objects."]
        for i, entry in enumerate(data, 1):
            if not isinstance(entry, dict):
                errors.append(f"Item {i}: not an object.")
                continue
            _convert_import_entry(
                f"Item {i}",
                str(entry.get("title", "")),
                str(entry.get("category", "misc")),
                str(entry.get("points", "100")),
                str(entry.get("flag", "")),
                str(entry.get("description", "")),
                items, errors,
            )
    return items, errors


@router.get("/challenges/import")
def import_form(request: Request, _admin: User = Depends(require_admin)):
    """The preview arrives via the PRG query string (?items=<json>).
    A bad `items` value is tolerated — it just means no preview."""
    error = request.query_params.get("error", "")
    items_raw = request.query_params.get("items", "")
    items: list[dict] = []
    if items_raw:
        try:
            parsed = json.loads(items_raw)
            if isinstance(parsed, list):
                items = parsed
        except (json.JSONDecodeError, ValueError):
            items = []
    return templates.TemplateResponse(
        request=request, name="admin/import.html",
        context={"preview": items or None, "errors": [error] if error else [],
                 "items_json": items_raw},
    )


@router.post("/challenges/import")
async def import_preview(
    request: Request,
    payload: str = Form(""),
    format: str = Form("json"),
    _admin: User = Depends(require_admin),
    _: None = Depends(verify_csrf),
):
    items, errors = _parse_import(payload, format)
    if not items:
        # PRG: parse failure goes back to the form with the error.
        errors = errors or ["Nothing importable was found."]
        return RedirectResponse(
            "/admin/challenges/import?error=" + quote("; ".join(errors), safe=""),
            status_code=303,
        )
    # PRG: the parsed items travel in the query string; the GET page
    # renders the review table from them.
    return RedirectResponse(
        "/admin/challenges/import?items=" + quote(json.dumps(items), safe="")
        + ("&error=" + quote("; ".join(errors), safe="") if errors else ""),
        status_code=303,
    )


@router.post("/challenges/import/confirm")
async def import_confirm(
    request: Request,
    items_json: str = Form(""),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    _: None = Depends(verify_csrf),
):
    try:
        items = json.loads(items_json)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Bad import payload")
    if not isinstance(items, list) or not items or len(items) > IMPORT_MAX_ITEMS:
        raise HTTPException(status_code=400, detail="Bad import payload")
    created = 0
    for entry in items:
        # Re-validate the hash on confirm (defense in depth).
        flag_hash = str(entry.get("flag_sha256", "")).strip()
        if not re.fullmatch(r"[0-9a-f]{64}", flag_hash):
            continue
        challenge = Challenge(
            title=str(entry.get("title", ""))[:200],
            category=re.sub(r"\s+", "-", str(entry.get("category", "misc")).strip().lower())[:32] or "misc",
            description=str(entry.get("description", "")),
            points=int(entry.get("points", 100)),
            author_id=admin.id,
            flag_sha256=flag_hash,
            hints=[],
            is_published=False,  # drafts: the author reviews, then publishes
        )
        db.add(challenge)
        created += 1
    db.commit()
    return RedirectResponse(
        f"/admin/challenges?msg=Imported {created} challenges as drafts — review and publish.",
        status_code=303,
    )


# --- user management (Phase 5) -------------------------------------------------

def _users_page(request: Request, db: Session, q: str = "", msg: str = "",
                new_password: str = "", new_password_for: str = ""):
    query = db.query(User).order_by(User.id)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(User.username.ilike(like), User.email.ilike(like)))
    users = query.all()
    return templates.TemplateResponse(
        request=request, name="admin/users.html",
        context={"users": users, "q": q, "msg": msg,
                 "new_password": new_password, "new_password_for": new_password_for},
    )


@router.get("/users")
def users_list(
    request: Request,
    q: str = "",
    msg: str = "",
    reset_token: str = "",
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """A one-time reset password is revealed via a single-use token
    (?reset_token=...) — the password itself NEVER appears in a URL.
    The token is popped on first use, so a refresh shows nothing."""
    new_password = ""
    new_password_for = ""
    if reset_token:
        taken = one_time_passwords.take(reset_token)
        if taken:
            new_password_for, new_password = taken
    return _users_page(request, db, q=q, msg=msg,
                       new_password=new_password, new_password_for=new_password_for)


@router.post("/users/{user_id}/role")
def user_change_role(
    user_id: int,
    request: Request,
    role: str = Form(""),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    _: None = Depends(verify_csrf),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404)
    if user.id == admin.id:
        return RedirectResponse("/admin/users?msg=You cannot change your own role.",
                                status_code=303)
    if role in VALID_ROLES:
        user.role = role
        db.commit()
    return RedirectResponse("/admin/users?msg=Role updated.", status_code=303)


@router.post("/users/{user_id}/ban")
def user_ban(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    _: None = Depends(verify_csrf),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404)
    if user.id == admin.id:
        return RedirectResponse("/admin/users?msg=You cannot ban yourself.",
                                status_code=303)
    user.is_banned = not user.is_banned
    db.commit()
    # The per-request user reload (middleware) kills banned sessions at once.
    return RedirectResponse(
        f"/admin/users?msg={'Unbanned' if not user.is_banned else 'Banned'} {user.username}.",
        status_code=303,
    )


@router.post("/users/{user_id}/delete")
def user_delete(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    _: None = Depends(verify_csrf),
):
    """Permanently delete a user and everything that belongs to them
    alone: solves, submissions, instances, hint unlocks, team
    memberships and cheat-log rows (as offender or as flag owner).

    Teams they captain are deleted too (a team without its captain
    cannot be led). Their authored challenges and announcements stay
    — those are platform content, not personal data. You cannot delete
    yourself. This cannot be undone."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404)
    if user.id == admin.id:
        return RedirectResponse("/admin/users?msg=You cannot delete yourself.",
                                status_code=303)
    username = user.username
    db.query(Solve).filter(Solve.user_id == user.id).delete()
    db.query(Submission).filter(Submission.user_id == user.id).delete()
    db.query(Instance).filter(Instance.user_id == user.id).delete()
    db.query(HintUnlock).filter(HintUnlock.user_id == user.id).delete()
    db.query(CheatLog).filter(
        or_(CheatLog.user_id == user.id, CheatLog.flag_owner_id == user.id)
    ).delete()
    captained = db.query(Team).filter(Team.captain_id == user.id).all()
    for team in captained:
        db.query(TeamMembership).filter(TeamMembership.team_id == team.id).delete()
        db.delete(team)
    db.query(TeamMembership).filter(TeamMembership.user_id == user.id).delete()
    db.delete(user)
    db.commit()
    return RedirectResponse(
        f"/admin/users?msg=Deleted user {username}.", status_code=303,
    )


@router.post("/users/{user_id}/password")
def user_reset_password(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    _: None = Depends(verify_csrf),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404)
    new_password = _generate_password()
    user.password_hash = hash_password(new_password)
    db.commit()
    # v3.0: the password NEVER travels in the URL. Only a random,
    # single-use token does (OneTimePasswordStore in app/security.py);
    # the users page pops the password once and a refresh shows nothing.
    # The old ?new_password=... redirect leaked the password into
    # browser history and server access logs.
    token = one_time_passwords.store(user.username, new_password)
    return RedirectResponse(
        "/admin/users?msg=" + quote("Password reset.", safe="")
        + "&reset_token=" + quote(token, safe=""),
        status_code=303,
    )


# --- submissions & cheat log (Phase 5) ------------------------------------------

EXPORT_PDF_MAX_ROWS = 1000


@router.get("/submissions/export.pdf")
def submissions_export_pdf(
    request: Request,
    user: str = "",
    challenge: str = "",
    correct: str = "",
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin-only PDF export of the submission log (v2).

    Plaintext flags are NEVER stored, so the PDF shows submitted-hash
    prefixes only. Capped at the latest 1000 rows, with a note when the
    cap bites. v2.8: accepts the same filters as the submissions list
    (user / challenge / correct) so an admin can export one user's log
    for cheating analysis."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    # One filtered base query drives the stats, the breakdown and the
    # rows, so the PDF always matches the filtered list view.
    base = (
        db.query(Submission, User.username, Challenge.title)
        .join(User, User.id == Submission.user_id)
        .join(Challenge, Challenge.id == Submission.challenge_id)
    )
    if user:
        base = base.filter(User.username == user)
    if challenge.isdigit():
        base = base.filter(Submission.challenge_id == int(challenge))
    if correct == "1":
        base = base.filter(Submission.is_correct.is_(True))
    elif correct == "0":
        base = base.filter(Submission.is_correct.is_(False))

    total = base.count()
    correct_n = base.filter(Submission.is_correct.is_(True)).count()
    unique_users = (
        base.with_entities(func.count(func.distinct(Submission.user_id))).scalar() or 0
    )
    unique_challenges = (
        base.with_entities(func.count(func.distinct(Submission.challenge_id))).scalar() or 0
    )

    breakdown = (
        base.with_entities(Challenge.title, func.count().label("attempts"),
                           func.sum(Submission.is_correct.cast(Integer)).label("correct_n"))
        .group_by(Challenge.id, Challenge.title)
        .order_by(func.count().desc())
        .all()
    )

    rows = (
        base.order_by(Submission.submitted_at.desc())
        .limit(EXPORT_PDF_MAX_ROWS)
        .all()
    )
    capped = total > EXPORT_PDF_MAX_ROWS

    buf = io.BytesIO()
    styles = getSampleStyleSheet()
    normal = styles["Normal"]
    heading = styles["Heading1"]
    # v2.8: a real child style — the old code did `small = styles["Normal"]`
    # and then mutated fontSize, which silently shrank EVERY "Normal"
    # paragraph in the PDF to 9pt too (same object).
    small = ParagraphStyle("Small", parent=styles["Normal"], fontSize=9, leading=11)

    def _p(text: str, style=normal):
        # Dynamic text (challenge titles, usernames) is XML-escaped:
        # Paragraph parses its input as markup, and a raw "<b" or
        # "<script>" in a title used to 500 the whole export with a
        # paraparser error — and "<...>" fragments were silently
        # swallowed from the PDF even when it didn't crash.
        return Paragraph(_xml_escape(text), style)

    def _raw(markup: str, style=normal):
        # Only for our own fixed markup (<b> headers, &nbsp; separators).
        return Paragraph(markup, style)

    story = [
        _p("BYTE_BATALLION_CTF_PLATFORM — Submission Log", heading),
        _p(f"Generated at {to_ist(datetime.now(timezone.utc)).strftime('%Y-%m-%d %H:%M:%S')} IST", small),
        Spacer(1, 12),
    ]
    if user or challenge or correct:
        parts = []
        if user:
            parts.append(f"User: {user}")
        if challenge.isdigit():
            parts.append(f"Challenge ID: {challenge}")
        if correct == "1":
            parts.append("Result: correct")
        elif correct == "0":
            parts.append("Result: incorrect")
        story.append(_p("Filtered by " + " · ".join(parts), small))
        story.append(Spacer(1, 6))
    story += [
        _p(f"Total submissions: {total}", normal),
        _raw(f"Correct: {correct_n} &nbsp;|&nbsp; Incorrect: {total - correct_n}", normal),
        _raw(f"Unique users: {unique_users} &nbsp;|&nbsp; Unique challenges: {unique_challenges}", normal),
        _p("Plaintext flags are NEVER stored — this log shows submitted-hash prefixes only.", small),
        Spacer(1, 12),
        _p("Per-challenge breakdown", styles["Heading2"]),
    ]
    breakdown_data = [[_raw("<b>Challenge</b>"), _raw("<b>Attempts</b>"), _raw("<b>Correct</b>")]]
    for title, attempts, correct_count in breakdown:
        breakdown_data.append([_p(title), _p(str(attempts)), _p(str(correct_count or 0))])
    if len(breakdown_data) == 1:
        breakdown_data.append([_p("No submissions yet."), _p(""), _p("")])
    breakdown_table = Table(breakdown_data, colWidths=[320, 80, 80])
    breakdown_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
    ]))
    story.append(breakdown_table)
    story.append(Spacer(1, 12))
    story.append(_p("Submission log (latest first)", styles["Heading2"]))
    if capped:
        story.append(_p(
            f"Note: showing the latest {EXPORT_PDF_MAX_ROWS} of {total} submissions.",
            small,
        ))
    log_data = [[_raw("<b>Time (IST)</b>"), _raw("<b>User</b>"), _raw("<b>Challenge</b>"),
                 _raw("<b>Hash (first 12)</b>"), _raw("<b>Result</b>")]]
    for s, username, title in rows:
        log_data.append([
            _p(format_ist(s.submitted_at, "%Y-%m-%d %H:%M:%S") if s.submitted_at else ""),
            _p(username),
            _p(title),
            _p((s.submitted_flag_hash or "")[:12]),
            _p("correct" if s.is_correct else "incorrect"),
        ])
    if len(log_data) == 1:
        log_data.append([_p("No submissions yet."), _p(""), _p(""), _p(""), _p("")])
    log_table = Table(log_data, colWidths=[110, 80, 130, 80, 60])
    log_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
    ]))
    story.append(log_table)

    SimpleDocTemplate(buf, pagesize=A4,
                      title="BYTE_BATALLION_CTF_PLATFORM — Submission Log").build(story)
    filename = f"bytebatallion-submissions-{user}.pdf" if user else "bytebatallion-submissions.pdf"
    return Response(
        buf.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/submissions")
def submissions_list(
    request: Request,
    user: str = "",
    challenge: str = "",
    correct: str = "",
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = (
        db.query(Submission, User.username, Challenge.title)
        .join(User, User.id == Submission.user_id)
        .join(Challenge, Challenge.id == Submission.challenge_id)
        .order_by(Submission.submitted_at.desc())
    )
    if user:
        # v2.8: exact match — the user filter is a dropdown now, and a
        # substring match ("grace" also matching "grace2") is wrong
        # evidence when analysing one user for cheating.
        query = query.filter(User.username == user)
    if challenge.isdigit():
        query = query.filter(Submission.challenge_id == int(challenge))
    if correct == "1":
        query = query.filter(Submission.is_correct.is_(True))
    elif correct == "0":
        query = query.filter(Submission.is_correct.is_(False))
    rows = query.limit(200).all()
    challenges = db.query(Challenge).order_by(Challenge.title).all()
    users = db.query(User).order_by(User.username).all()
    # v2.8: per-user evidence summary — totals + first/last activity
    # at a glance when one user is selected.
    user_summary = None
    if user:
        target = db.query(User).filter(User.username == user).first()
        if target is not None:
            sq = db.query(Submission).filter(Submission.user_id == target.id)
            total_u = sq.count()
            correct_u = sq.filter(Submission.is_correct.is_(True)).count()
            first_u = db.query(func.min(Submission.submitted_at)).filter(
                Submission.user_id == target.id).scalar()
            last_u = db.query(func.max(Submission.submitted_at)).filter(
                Submission.user_id == target.id).scalar()
            challenges_u = db.query(func.count(func.distinct(Submission.challenge_id))).filter(
                Submission.user_id == target.id).scalar() or 0
            user_summary = {
                "username": target.username,
                "total": total_u,
                "correct": correct_u,
                "incorrect": total_u - correct_u,
                "challenges": challenges_u,
                "first": first_u,
                "last": last_u,
            }
    return templates.TemplateResponse(
        request=request, name="admin/submissions.html",
        context={"rows": rows, "challenges": challenges, "users": users,
                 "user_summary": user_summary,
                 "rejected_keys": {
                     (s.user_id, s.challenge_id)
                     for s in db.query(Solve).filter(Solve.is_rejected.is_(True)).all()
                 },
                 "f_user": user, "f_challenge": challenge, "f_correct": correct},
    )


def _solve_action_redirect(request: Request, return_to: str) -> RedirectResponse:
    """Back to the submissions list the admin came from. Only a
    same-site /admin/submissions URL is honoured — anything else (or
    nothing) falls back to the plain list."""
    if return_to.startswith("/admin/submissions"):
        return RedirectResponse(return_to, status_code=303)
    return RedirectResponse("/admin/submissions", status_code=303)


@router.post("/solves/reject")
def solve_reject(
    request: Request,
    user_id: int = Form(0),
    challenge_id: int = Form(0),
    return_to: str = Form(""),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    _: None = Depends(verify_csrf),
):
    """Reject a player's solve as suspected cheating.

    The solve stops scoring ANYWHERE (scoreboard, board counts, first
    blood, profile) and the challenge locks for that player — they
    cannot submit it again. `points_awarded` is NOT overwritten, so
    Recover restores the points exactly. The submission log itself is
    untouched (audit trail)."""
    solve = db.get(Solve, (user_id, challenge_id))
    if solve is not None and not solve.is_rejected:
        solve.is_rejected = True
        db.commit()
    return _solve_action_redirect(request, return_to)


@router.post("/solves/recover")
def solve_recover(
    request: Request,
    user_id: int = Form(0),
    challenge_id: int = Form(0),
    return_to: str = Form(""),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    _: None = Depends(verify_csrf),
):
    """Undo a rejection: the solve scores again and the challenge
    unlocks for the player. Only a rejected solve can be recovered."""
    solve = db.get(Solve, (user_id, challenge_id))
    if solve is not None and solve.is_rejected:
        solve.is_rejected = False
        db.commit()
    return _solve_action_redirect(request, return_to)


@router.get("/cheat-log")
def cheat_log(
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    offender = aliased(User)
    owner = aliased(User)
    rows = (
        db.query(CheatLog, offender, owner, Challenge.title)
        .join(offender, offender.id == CheatLog.user_id)
        .join(owner, owner.id == CheatLog.flag_owner_id)
        .join(Challenge, Challenge.id == CheatLog.challenge_id)
        .order_by(CheatLog.detected_at.desc())
        .limit(200)
        .all()
    )
    return templates.TemplateResponse(
        request=request, name="admin/cheat_log.html", context={"rows": rows},
    )


# --- announcements (Phase 5) ------------------------------------------------------

@router.get("/announcements")
def announcements_list(
    request: Request,
    msg: str = "",
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = db.query(Announcement).order_by(Announcement.created_at.desc()).all()
    return templates.TemplateResponse(
        request=request, name="admin/announcements.html",
        context={"rows": rows, "msg": msg},
    )


@router.post("/announcements/new")
def announcement_create(
    request: Request,
    title: str = Form(""),
    body: str = Form(""),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    _: None = Depends(verify_csrf),
):
    title, body = title.strip(), body.strip()
    if title and body:
        db.add(Announcement(title=title[:200], body=body, created_by=admin.id))
        db.commit()
    return RedirectResponse("/admin/announcements", status_code=303)


@router.get("/announcements/{a_id}/edit")
def announcement_edit_form(
    a_id: int,
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    a = db.get(Announcement, a_id)
    if a is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request, name="admin/announcement_form.html", context={"a": a},
    )


@router.post("/announcements/{a_id}/edit")
def announcement_edit(
    a_id: int,
    request: Request,
    title: str = Form(""),
    body: str = Form(""),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    _: None = Depends(verify_csrf),
):
    a = db.get(Announcement, a_id)
    if a is None:
        raise HTTPException(status_code=404)
    title, body = title.strip(), body.strip()
    if title and body:
        a.title, a.body = title[:200], body
        db.commit()
    return RedirectResponse("/admin/announcements?msg=Announcement updated.", status_code=303)


@router.post("/announcements/{a_id}/delete")
def announcement_delete(
    a_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    _: None = Depends(verify_csrf),
):
    a = db.get(Announcement, a_id)
    if a is not None:
        db.delete(a)
        db.commit()
    return RedirectResponse("/admin/announcements?msg=Announcement deleted.", status_code=303)


# --- custom pages (Phase 6) ---------------------------------------------------------

@router.get("/pages")
def pages_list(
    request: Request,
    msg: str = "",
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = db.query(Page).order_by(Page.title).all()
    return templates.TemplateResponse(
        request=request, name="admin/pages.html", context={"rows": rows, "msg": msg},
    )


def _check_page_fields(db: Session, title: str, slug: str,
                       page_id: int) -> list[str]:
    """Slug rules: lowercase/digits/hyphens, unique across pages."""
    errors: list[str] = []
    slug = slug.strip().lower()
    if not title:
        errors.append("Title is required.")
    if len(title) > 200:
        errors.append("Title is too long (max 200 characters).")
    if not slug or not SLUG_RE.fullmatch(slug) or len(slug) > PAGE_MAX_SLUG:
        errors.append(
            "Slug may only contain lowercase letters, digits and hyphens "
            "(e.g. rules, faq, contact)."
        )
    elif db.query(Page).filter(
        Page.slug == slug, Page.id != page_id
    ).first() is not None:
        errors.append("A page with this slug already exists.")
    return errors


def _page_from_form(page: Page | None, db: Session,
                    title: str, slug: str, content: str, publish_action: str) -> list[str]:
    """Validate the page form; returns the error list (empty = valid).
    Callers redirect back to the form with ?error=... on failure (PRG)."""
    return _check_page_fields(db, title, slug, page.id if page else 0)


@router.get("/pages/new")
def page_new_form(request: Request, _admin: User = Depends(require_admin)):
    error = request.query_params.get("error", "")
    return templates.TemplateResponse(
        request=request, name="admin/page_form.html",
        context={"page": None, "form": {"title": "", "slug": "", "content": "",
                                        "is_published": True},
                 "errors": [error] if error else []},
    )


@router.post("/pages/new")
def page_create(
    request: Request,
    title: str = Form(""),
    slug: str = Form(""),
    content: str = Form(""),
    is_published: str = Form(""),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    _: None = Depends(verify_csrf),
):
    errors = _page_from_form(
        None, db, title.strip(), slug.strip().lower(), content,
        is_published == "on",
    )
    if errors:
        # PRG: error in the query string (field values are lost).
        return RedirectResponse(
            "/admin/pages/new?error=" + quote("; ".join(errors), safe=""),
            status_code=303,
        )
    page = Page(title=title.strip(), slug=slug.strip().lower(),
                content=content, is_published=is_published == "on")
    db.add(page)
    db.commit()
    return RedirectResponse("/admin/pages?msg=Page created.", status_code=303)


@router.get("/pages/{page_id}/edit")
def page_edit_form(
    page_id: int,
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    page = db.get(Page, page_id)
    if page is None:
        raise HTTPException(status_code=404)
    error = request.query_params.get("error", "")
    return templates.TemplateResponse(
        request=request, name="admin/page_form.html",
        context={"page": page, "form": page,
                 "errors": [error] if error else []},
    )


@router.post("/pages/{page_id}/edit")
def page_edit(
    page_id: int,
    request: Request,
    title: str = Form(""),
    slug: str = Form(""),
    content: str = Form(""),
    is_published: str = Form(""),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    _: None = Depends(verify_csrf),
):
    page = db.get(Page, page_id)
    if page is None:
        raise HTTPException(status_code=404)
    errors = _page_from_form(
        page, db, title.strip(), slug.strip().lower(), content,
        is_published == "on",
    )
    if errors:
        # PRG: error in the query string (field values are lost).
        return RedirectResponse(
            f"/admin/pages/{page_id}/edit?error=" + quote("; ".join(errors), safe=""),
            status_code=303,
        )
    page.title = title.strip()
    page.slug = slug.strip().lower()
    page.content = content
    page.is_published = is_published == "on"
    db.commit()
    return RedirectResponse("/admin/pages?msg=Page updated.", status_code=303)


@router.post("/pages/{page_id}/delete")
def page_delete(
    page_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    _: None = Depends(verify_csrf),
):
    page = db.get(Page, page_id)
    if page is not None:
        db.delete(page)
        db.commit()
    return RedirectResponse("/admin/pages?msg=Page deleted.", status_code=303)


# --- config (Phase 5) --------------------------------------------------------------

@router.get("/config")
def config_page(
    request: Request,
    msg: str = "",
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    cfg = ctf_state(db)
    return templates.TemplateResponse(
        request=request, name="admin/config.html",
        context={
            "cfg": cfg, "msg": msg,
            "start_val": cfg["start"].strftime("%Y-%m-%dT%H:%M") if cfg["start"] else "",
            "end_val": cfg["end"].strftime("%Y-%m-%dT%H:%M") if cfg["end"] else "",
        },
    )


@router.post("/config")
def config_save(
    request: Request,
    platform_name: str = Form(""),
    ctf_start_time: str = Form(""),
    ctf_end_time: str = Form(""),
    registration_open: str = Form(""),
    scoreboard_visible: str = Form(""),
    teams_mode: str = Form(""),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    _: None = Depends(verify_csrf),
):
    set_config(db, "platform_name", platform_name.strip() or "BYTE_BATALLION_CTF_PLATFORM")
    errors = []
    for key, raw in (("ctf_start_time", ctf_start_time), ("ctf_end_time", ctf_end_time)):
        raw = raw.strip()
        if not raw:
            set_config(db, key, "")
            continue
        try:
            dt = datetime.fromisoformat(raw)
            set_config(db, key, dt.isoformat())
        except ValueError:
            errors.append(f"Invalid date/time for {key.replace('ctf_', '').replace('_time', '')}.")
    set_config(db, "registration_open", "true" if registration_open == "on" else "false")
    set_config(db, "scoreboard_visible", "true" if scoreboard_visible == "on" else "false")
    set_config(db, "teams_mode", "true" if teams_mode == "on" else "false")
    if errors:
        return RedirectResponse(f"/admin/config?msg={'; '.join(errors)} (other settings saved.)",
                                status_code=303)
    return RedirectResponse("/admin/config?msg=Config saved.", status_code=303)


# --- export (Phase 5) ----------------------------------------------------------------

@router.get("/export")
def export_page(request: Request, _admin: User = Depends(require_admin)):
    return templates.TemplateResponse(request=request, name="admin/export.html", context={})


@router.get("/export/challenges.json")
def export_challenges(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    data = []
    for c in db.query(Challenge).order_by(Challenge.id).all():
        data.append({
            "id": c.id,
            "title": c.title,
            "category": c.category,
            "description": c.description,
            "points": c.points,
            "max_attempts": c.max_attempts,
            "flag_template": c.flag_template,
            "flag_sha256": c.flag_sha256,
            "hints": c.hints or [],
            "is_published": c.is_published,
            "is_deleted": c.is_deleted,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        })
    return Response(
        json.dumps(data, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=challenges.json"},
    )


@router.get("/export/results.csv")
def export_results(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["rank", "username", "email", "points", "solves", "last_solve_utc"])
    for rank, row in enumerate(ranked_players(db), 1):
        writer.writerow([
            rank,
            row.username,
            row.email,
            row.points,
            row.solve_count,
            row.last_solve.isoformat() if row.last_solve else "",
        ])
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=results.csv"},
    )
