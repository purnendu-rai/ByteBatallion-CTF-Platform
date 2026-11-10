"""Database models — exactly the tables from the master brief, Section 5.

RULE: flags are NEVER stored in plaintext. `instances` and `submissions`
hold SHA-256 hashes only.
"""

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.database import Base, SessionLocal
from app.timeutil import IST


def utc_now() -> datetime:
    """Timezone-aware UTC timestamp used by every created/solved column."""
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # player | author | admin (only an admin can change roles — Phase 1)
    role: Mapped[str] = mapped_column(String(16), default="player")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)


# Fixed category list (brief, Section 5)
CHALLENGE_CATEGORIES = ["web", "crypto", "forensics", "pwn", "rev", "osint", "misc"]


class Challenge(Base):
    __tablename__ = "challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    # web | crypto | forensics | pwn | rev | osint | misc
    category: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(Text, default="")
    points: Mapped[int] = mapped_column(Integer, default=100)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    # e.g. `BB{web_sqli_{RAND:12}}` — validated in Phase 3 (flag engine).
    flag_template: Mapped[str] = mapped_column(String(255), default="")
    # SHA-256 of the SINGLE challenge flag the admin sets when creating the
    # challenge. Users must submit exactly this flag to solve (simple mode).
    # Plaintext is never stored — only this hash.
    flag_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    files_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hints: Mapped[list] = mapped_column(JSON, default=list)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    # Soft delete (Phase 2): deleted challenges keep their solves in the DB.
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Instance(Base):
    """One generated per-user flag. Exactly one ACTIVE row per
    (user, challenge) — enforced in application logic (Phase 3).

    The plaintext flag exists only inside the player's generated files;
    the database stores its SHA-256 hash and nothing else.
    """

    __tablename__ = "instances"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    challenge_id: Mapped[int] = mapped_column(ForeignKey("challenges.id"), index=True)
    flag_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Submission(Base):
    """Every attempt is logged, right or wrong (hash only, never plaintext)."""

    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    challenge_id: Mapped[int] = mapped_column(ForeignKey("challenges.id"), index=True)
    submitted_flag_hash: Mapped[str] = mapped_column(String(64))
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Solve(Base):
    """One row per (user, challenge) — a second correct submit awards nothing."""

    __tablename__ = "solves"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    challenge_id: Mapped[int] = mapped_column(ForeignKey("challenges.id"), primary_key=True)
    solved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    points_awarded: Mapped[int] = mapped_column(Integer)


class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class ConfigEntry(Base):
    """Key/value platform config: name, start/end time, registration_open,
    scoreboard_visible (Phase 5 admin config page)."""

    __tablename__ = "config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))


# --- Phase 6:Teams (config toggle `teams_mode`) -------------------------------


class Team(Base):
    """A team of at most TEAM_MAX members (captain included).

    Solves stay per-user; the team scoreboard aggregates them. Nothing
    here ever modifies solo data — toggling teams mode off just runs the
    platform solo again.
    """

    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    captain_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    # Join/shareable code — generated with `secrets`, never `random`.
    invite_code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TeamMembership(Base):
    """One row per (team, user) — a player is in at most one team."""

    __tablename__ = "team_members"

    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class HintUnlock(Base):
    """A paid hint was VIEWED by this player (Phase 6).

    Paid hints charge points exactly once — on first view, which is this
    row. `points_charged` is capped at the player's current total so a
    score can never go below 0 (brief §12). Solo scoreboard/profile sums
    solve points MINUS every unlock charge for that user.
    """

    __tablename__ = "hint_unlocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    challenge_id: Mapped[int] = mapped_column(ForeignKey("challenges.id"), index=True)
    # Position of the hint inside the challenge's hint list.
    hint_index: Mapped[int] = mapped_column(Integer)
    points_charged: Mapped[int] = mapped_column(Integer, default=0)
    unlocked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Page(Base):
    """Custom static pages (rules, FAQ, contact) — Phase 6.

    Published pages appear as nav links on every page; the slug is the
    URL part (/pages/<slug>)."""

    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    is_published: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CheatLog(Base):
    """Written when a player submits a flag that belongs to ANOTHER user.

    The offending player is only ever told "Incorrect" — never that it was
    someone else's flag. This table is admin-only (Phase 5 UI).
    """

    __tablename__ = "cheat_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    challenge_id: Mapped[int] = mapped_column(ForeignKey("challenges.id"), index=True)
    submitted_flag_hash: Mapped[str] = mapped_column(String(64))
    flag_owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    source_ip: Mapped[str] = mapped_column(String(64))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


# --- platform config helpers (Phase 5) ---------------------------------------


# Phase 6: a team has at most 4 members (captain included).
TEAM_MAX = 4


def get_config(db: Session, key: str, default: str = "") -> str:
    row = db.get(ConfigEntry, key)
    return row.value if row is not None else default


def set_config(db: Session, key: str, value: str) -> None:
    row = db.get(ConfigEntry, key)
    if row is None:
        db.add(ConfigEntry(key=key, value=value))
    else:
        row.value = value
    db.commit()


def _parse_config_dt(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # datetime-local inputs are naive — the admin enters IST times.
        dt = dt.replace(tzinfo=IST)
    return dt


def ctf_state(db: Session) -> dict:
    """Parsed platform config + the current CTF phase (before/running/ended)."""
    start = _parse_config_dt(get_config(db, "ctf_start_time"))
    end = _parse_config_dt(get_config(db, "ctf_end_time"))
    now = datetime.now(timezone.utc)
    phase = "running"
    if start is not None and now < start:
        phase = "before"
    elif end is not None and now > end:
        phase = "ended"
    return {
        "name": get_config(db, "platform_name", "BYTE_BATALLION_CTF_PLATFORM") or "BYTE_BATALLION_CTF_PLATFORM",
        "start": start,
        "end": end,
        "phase": phase,
        "registration_open": get_config(db, "registration_open", "true") == "true",
        "scoreboard_visible": get_config(db, "scoreboard_visible", "true") == "true",
        # Phase 6: when OFF the platform runs solo only; turning it on
        # reveals team mode (pages hidden until then) but NEVER changes
        # any solo solve/score data.
        "teams_mode": get_config(db, "teams_mode", "false") == "true",
    }


# --- hint helpers (Phase 6) ---------------------------------------------------
#
# A challenge stores its hints as a JSON list. Phase 6 adds per-hint
# point costs, so entries are dicts {"text": ..., "cost": ...}. Older
# phase-2 challenges may still hold plain strings — those are FREE hints
# (cost 0). Normalise at every read site so both shapes work forever.


def normalized_hints(raw) -> list[dict]:
    """The challenge's hints as [{"index": i, "text": ..., "cost": ...}].
    `index` is the position in the ORIGINAL list (used as the unlock key)."""
    out: list[dict] = []
    for i, hint in enumerate(raw or []):
        if isinstance(hint, dict):
            text = str(hint.get("text") or "").strip()
            try:
                cost = int(hint.get("cost", 0))
            except (TypeError, ValueError):
                cost = 0
        else:  # legacy plain-string hint from Phase 2 — free
            text = str(hint).strip()
            cost = 0
        if text:
            out.append({"index": i, "text": text, "cost": max(0, min(cost, 100000))})
    return out


def load_public_config() -> dict:
    """Per-request config snapshot for templates (called by middleware)."""
    db = SessionLocal()
    try:
        return ctf_state(db)
    finally:
        db.close()
