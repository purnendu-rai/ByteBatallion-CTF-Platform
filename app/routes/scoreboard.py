"""Scoreboard & profile routes.

Profile page: Phase 1. Scoreboard: Phase 4 (ranked by points, tie-break
by earliest last-solve time, visibility follows the config table).
Phase 6: paid hint charges are subtracted everywhere, and — when the
`teams_mode` config toggle is ON — a team view aggregates the members'
solves CTFd-style (a challenge solved by any member counts once).
"""

import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    Challenge,
    HintUnlock,
    Solve,
    Submission,
    Team,
    TeamMembership,
    User,
    get_config,
)
from app.security import require_user

router = APIRouter(tags=["scoreboard"])
from app.timeutil import format_ist
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
templates.env.filters["ist"] = format_ist

_DT_MAX = datetime.datetime.max  # naive; only used as an ordering sentinel


def _hint_charges_for_users(db: Session) -> dict[int, int]:
    """Per-user sum of paid-hint charges (Phase 6)."""
    return dict(
        db.query(HintUnlock.user_id, func.sum(HintUnlock.points_charged))
        .group_by(HintUnlock.user_id)
        .all()
    )


def ranked_players(db: Session):
    """All players ranked: points DESC, then earliest last-solve ASC.

    Points are solve points MINUS the player's paid-hint charges (never
    negative, because each charge was capped at unlock time). Shared by
    the scoreboard page and the admin CSV export (Phase 5)."""
    charges_sq = (
        db.query(
            HintUnlock.user_id.label("uid"),
            func.sum(HintUnlock.points_charged).label("charged"),
        )
        .group_by(HintUnlock.user_id)
        .subquery()
    )
    points_col = (
        func.coalesce(func.sum(Solve.points_awarded), 0)
        - func.coalesce(charges_sq.c.charged, 0)
    ).label("points")
    last_solve_col = func.max(Solve.solved_at).label("last_solve")
    return (
        db.query(
            User.id,
            User.username,
            User.email,
            points_col,
            func.count(Solve.challenge_id).label("solve_count"),
            last_solve_col,
        )
        # The rejected condition sits INSIDE the join (not a WHERE
        # filter) so a player whose only solves were rejected still
        # ranks — with 0 points — instead of vanishing.
        .outerjoin(Solve, (Solve.user_id == User.id)
                   & (Solve.is_rejected.is_(False)))
        .outerjoin(charges_sq, charges_sq.c.uid == User.id)
        .filter(User.role == "player", User.is_banned.is_(False))
        .group_by(User.id)
        .order_by(points_col.desc(), last_solve_col.asc())
        .all()
    )


def ranked_teams(db: Session) -> list[dict]:
    """Team scoreboard rows (Phase 6, `teams_mode` ON).

    CTFd-style team scoring: a challenge solved by ANY member of the
    team counts exactly ONCE for the team, valued at its challenge
    points. Members' paid-hint charges are subtracted. Tie-break by the
    EARLIEST last solve in the team. Banned members contribute nothing;
    turning teams mode OFF simply hides this table (no solo data is
    ever modified).
    """
    if db.query(Team).first() is None:
        return []

    # One row per (team, challenge): the team's first solve of it — and
    # the challenge's point value joined in for the team sum.
    first_solves = (
        db.query(
            TeamMembership.team_id.label("tid"),
            Challenge.points.label("pts"),
            func.max(Solve.solved_at).label("t"),
        )
        .join(Solve, Solve.user_id == TeamMembership.user_id)
        .join(User, User.id == TeamMembership.user_id)
        .join(Challenge, Challenge.id == Solve.challenge_id)
        .filter(User.is_banned.is_(False), Solve.is_rejected.is_(False))
        .group_by(TeamMembership.team_id, Solve.challenge_id, Challenge.points)
        .subquery()
    )
    totals = {
        row.tid: (row.points or 0, row.solves, row.last_solve)
        for row in (
            db.query(
                first_solves.c.tid,
                func.coalesce(func.sum(first_solves.c.pts), 0).label("points"),
                func.count(first_solves.c.tid).label("solves"),
                func.max(first_solves.c.t).label("last_solve"),
            )
            .group_by(first_solves.c.tid)
            .all()
        )
    }

    members = dict(
        db.query(TeamMembership.team_id, func.count())
        .join(User, User.id == TeamMembership.user_id)
        .filter(User.is_banned.is_(False))
        .group_by(TeamMembership.team_id)
        .all()
    )

    team_charges: dict[int, int] = {}
    charges = _hint_charges_for_users(db)
    if charges:
        for team_id, user_id in (
            db.query(TeamMembership.team_id, TeamMembership.user_id)
            .join(User, User.id == TeamMembership.user_id)
            .filter(User.is_banned.is_(False))
            .all()
        ):
            team_charges[team_id] = team_charges.get(team_id, 0) + charges.get(user_id, 0)

    rows = []
    for team in db.query(Team).order_by(Team.name).all():
        points, solves, last_solve = totals.get(team.id, (0, 0, None))
        rows.append({
            "team": team,
            "member_count": members.get(team.id, 0),
            "points": points - team_charges.get(team.id, 0),
            "solves": solves,
            "last_solve": last_solve,
        })
    rows.sort(key=lambda r: (-r["points"],
                             r["last_solve"].replace(tzinfo=None) if r["last_solve"] else _DT_MAX))
    return rows


def player_points(db: Session, user_id: int) -> int:
    """Solve points minus paid-hint charges for one player.

    Rejected solves score nothing."""
    points = (
        db.query(func.coalesce(func.sum(Solve.points_awarded), 0))
        .filter(Solve.user_id == user_id, Solve.is_rejected.is_(False))
        .scalar()
    )
    charged = (
        db.query(func.coalesce(func.sum(HintUnlock.points_charged), 0))
        .filter(HintUnlock.user_id == user_id)
        .scalar()
    )
    return int(points) - int(charged)


def player_team(db: Session, user_id: int) -> Team | None:
    """The single team this user belongs to, if any."""
    return (
        db.query(Team)
        .join(TeamMembership, TeamMembership.team_id == Team.id)
        .filter(TeamMembership.user_id == user_id)
        .first()
    )


# --- profile (Phase 1; Phase 6 adds net points + team) --------------------------


@router.get("/profile")
def profile(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Own profile: username, solves, points (net of hint charges), rank,
    team (when teams_mode is enabled), visual circular graph data, and solves log."""
    points = player_points(db, user.id)
    # A rejected solve is not a solve anywhere — not in the count,
    # the log, the category graph, or the rank.
    solve_count = (
        db.query(func.count()).select_from(Solve)
        .filter(Solve.user_id == user.id, Solve.is_rejected.is_(False)).scalar()
        or 0
    )

    rank = None
    # Only players are ranked: admin/author solves never appear on the leaderboard
    if user.role == "player" and solve_count > 0:
        ids_in_order = [row.id for row in ranked_players(db)]
        if user.id in ids_in_order:
            rank = ids_in_order.index(user.id) + 1

    teams_mode = get_config(db, "teams_mode", "false") == "true"
    team = player_team(db, user.id) if teams_mode else None
    team_name = team.name if team else None

    # Solves log with challenge name, category, points, and exact timestamp
    solves_query = (
        db.query(Solve, Challenge)
        .join(Challenge, Solve.challenge_id == Challenge.id)
        .filter(Solve.user_id == user.id, Solve.is_rejected.is_(False))
        .order_by(Solve.solved_at.desc())
        .all()
    )
    solves_log = [
        {
            "challenge_id": ch.id,
            "title": ch.title,
            "category": ch.category,
            "points": solve.points_awarded,
            "time_str": format_ist(solve.solved_at, "%B %d, %Y at %I:%M %p"),
            "solved_at": solve.solved_at,
        }
        for solve, ch in solves_query
    ]

    # Attempts stats for circular graph (correct vs incorrect submissions)
    correct_count = (
        db.query(func.count())
        .select_from(Submission)
        .filter(Submission.user_id == user.id, Submission.is_correct.is_(True))
        .scalar()
        or 0
    )
    fail_count = (
        db.query(func.count())
        .select_from(Submission)
        .filter(Submission.user_id == user.id, Submission.is_correct.is_(False))
        .scalar()
        or 0
    )
    total_attempts = correct_count + fail_count
    solves_pct = round((correct_count / total_attempts) * 100, 1) if total_attempts > 0 else 0.0
    fails_pct = round((fail_count / total_attempts) * 100, 1) if total_attempts > 0 else 0.0
    attempts_stats = {
        "correct": correct_count,
        "fails": fail_count,
        "total": total_attempts,
        "solves_pct": solves_pct,
        "fails_pct": fails_pct,
    }

    # Category stats for circular graph (solves per category)
    cat_counts = (
        db.query(Challenge.category, func.count(Solve.challenge_id))
        .join(Challenge, Solve.challenge_id == Challenge.id)
        .filter(Solve.user_id == user.id, Solve.is_rejected.is_(False))
        .group_by(Challenge.category)
        .all()
    )
    total_cat_solves = sum(cnt for _, cnt in cat_counts)
    category_stats = []
    for cat_name, cnt in sorted(cat_counts, key=lambda x: -x[1]):
        pct = round((cnt / total_cat_solves) * 100, 1) if total_cat_solves > 0 else 0.0
        category_stats.append({
            "category": cat_name,
            "count": cnt,
            "pct": pct,
        })

    # User score progression over time for profile line chart
    user_solves = (
        db.query(Solve.solved_at, Solve.points_awarded)
        .filter(Solve.user_id == user.id)
        .order_by(Solve.solved_at.asc())
        .all()
    )
    score_history = []
    cum = 0
    for s_at, pts in user_solves:
        cum += pts
        score_history.append({"time": _epoch_ms(s_at), "score": cum})
    if score_history:
        score_history.insert(0, {"time": score_history[0]["time"], "score": 0})

    return templates.TemplateResponse(
        request=request,
        name="profile.html",
        context={
            "user": user,
            "points": points,
            "solve_count": solve_count,
            "rank": rank,
            "team_name": team_name,
            "teams_mode": teams_mode,
            "solves_log": solves_log,
            "attempts_stats": attempts_stats,
            "category_stats": category_stats,
            "score_history": score_history,
        },
    )


@router.get("/scoreboard")
def scoreboard(
    request: Request,
    view: str = "",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Ranked by total points; tie-break by the EARLIEST last-solve time
    (you reached that score first). Only players are ranked.

    Phase 6: with teams_mode ON the default view is the team table,
    switchable back to players. Toggling teams mode never touches the
    solo data — the player table keeps working either way.
    """
    visible = get_config(db, "scoreboard_visible", "true") == "true"
    is_admin = user.role == "admin"
    can_view = visible or is_admin
    teams_mode = get_config(db, "teams_mode", "false") == "true"

    show_teams = can_view and teams_mode and view != "players"
    team_rows = ranked_teams(db) if show_teams else []
    player_rows = [] if show_teams else (ranked_players(db) if can_view else [])
    return templates.TemplateResponse(
        request=request,
        name="scoreboard.html",
        context={
            "rows": player_rows,
            "team_rows": team_rows,
            "show_teams": show_teams,
            "teams_mode": teams_mode,
            "view": "teams" if show_teams else "players",
            "can_view": can_view,
            "visible": visible,
        },
    )


# --- live score-progression graph (CTFd-style top-10 chart) ------------------

def _epoch_ms(dt: datetime.datetime) -> int:
    """Timezone-aware datetime -> epoch milliseconds (naive treated as UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1000)


def score_progression(db: Session, limit: int = 10, mode: str = "players") -> dict:
    """Build the top-N players' or teams' score-over-time series for the live graph.

    For each entity in the current top `limit` (by current points) we emit
    the cumulative-points step after every solve, ordered by solve time.
    """
    now_ms = _epoch_ms(datetime.datetime.now(datetime.timezone.utc))

    if mode == "teams":
        ranked = ranked_teams(db)
        top = ranked[:limit]
        series = []
        for row in top:
            team = row["team"]
            first_solves = (
                db.query(Challenge.points, func.min(Solve.solved_at).label("first_solved_at"))
                .join(Solve, Solve.challenge_id == Challenge.id)
                .join(TeamMembership, TeamMembership.user_id == Solve.user_id)
                .join(User, User.id == Solve.user_id)
                .filter(TeamMembership.team_id == team.id, User.is_banned.is_(False))
                .group_by(Challenge.id, Challenge.points)
                .order_by(func.min(Solve.solved_at).asc())
                .all()
            )
            points = []
            cumulative = 0
            for pts, solved_at in first_solves:
                cumulative += int(pts or 0)
                points.append([_epoch_ms(solved_at), cumulative])
            if points:
                points.insert(0, [points[0][0], 0])
            series.append({
                "username": team.name,
                "current_points": int(row["points"] or 0),
                "points": points,
            })
        return {"now": now_ms, "series": series}

    ranked = ranked_players(db)
    top = ranked[:limit]

    series = []
    for row in top:
        solves = (
            db.query(Solve.solved_at, Solve.points_awarded)
            .filter(Solve.user_id == row.id)
            .order_by(Solve.solved_at.asc())
            .all()
        )
        points = []
        cumulative = 0
        for solved_at, awarded in solves:
            cumulative += int(awarded or 0)
            points.append([_epoch_ms(solved_at), cumulative])
        # Start each line at 0 from the first solve time (or now if none),
        # so all lines share a common visual baseline.
        if points:
            points.insert(0, [points[0][0], 0])
        series.append({
            "username": row.username,
            "current_points": int(row.points or 0),
            "points": points,
        })

    return {"now": now_ms, "series": series}


@router.get("/scoreboard/top10.json")
def score_progression_json(
    request: Request,
    mode: str = "players",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Live data for the top-10 score graph (players or teams). Hidden scoreboard -> empty."""
    visible = get_config(db, "scoreboard_visible", "true") == "true"
    if not visible and user.role != "admin":
        return JSONResponse({"now": _epoch_ms(datetime.datetime.now(datetime.timezone.utc)),
                             "series": []})
    return JSONResponse(score_progression(db, limit=10, mode=mode))
