"""Teams mode (Phase 6, brief §12).

The whole feature is behind the `teams_mode` config toggle: when it is
OFF, these pages refuse to operate and the platform stays solo (solo
data is never touched — solve rows stay per user either way).

Rules:
- a player creates OR joins at most ONE team
- a team holds at most 4 members (captain included)
- join via the team's invite code (generated with `secrets`)
- leaving: captainship passes to the longest-standing member; an empty
  team is deleted
"""

import secrets
import string
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import TEAM_MAX, Team, TeamMembership, User, get_config
from app.routes.scoreboard import player_team, ranked_teams
from app.security import require_user, verify_csrf

router = APIRouter(prefix="/teams", tags=["teams"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))

TEAM_CODE_ALPHABET = string.ascii_uppercase + string.digits
TEAM_CODE_LEN = 8
TEAM_NAME_MAX = 64


def _teams_mode_on(db: Session) -> bool:
    return get_config(db, "teams_mode", "false") == "true"


def _new_invite_code(db: Session) -> str:
    """8-character uppercase join code (letters/digits via `secrets`)."""
    for _ in range(20):
        code = "".join(secrets.choice(TEAM_CODE_ALPHABET) for _ in range(TEAM_CODE_LEN))
        if db.query(Team).filter_by(invite_code=code).first() is None:
            return code
    raise HTTPException(status_code=500, detail="Could not generate invite code")


def _member_count(db: Session, team_id: int) -> int:
    return (
        db.query(TeamMembership)
        .filter_by(team_id=team_id)
        .count()
    )


def _require_teams_mode(db: Session) -> None:
    """Teams pages only exist when the admin turned teams mode ON."""
    if not _teams_mode_on(db):
        raise HTTPException(status_code=404)


@router.get("")
def teams_page(
    request: Request,
    msg: str = "",
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """My team (create / join via code / leave) + public team list."""
    _require_teams_mode(db)
    team = player_team(db, user.id)
    members = []
    if team is not None:
        members = (
            db.query(User.id, User.username, TeamMembership.joined_at)
            .join(TeamMembership, TeamMembership.user_id == User.id)
            .filter(TeamMembership.team_id == team.id)
            .order_by(TeamMembership.joined_at)
            .all()
        )
    return templates.TemplateResponse(
        request=request, name="teams.html",
        context={
            "team": team,
            "members": members,
            "rows": ranked_teams(db),
            "teams_mode": True,
            "msg": msg,
        },
    )


@router.post("/create")
def create_team(
    request: Request,
    name: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
):
    _require_teams_mode(db)
    name = name.strip()
    if player_team(db, user.id) is not None:
        return RedirectResponse("/teams?msg=You are already in a team.", status_code=303)
    if not name or len(name) > TEAM_NAME_MAX:
        return RedirectResponse("/teams?msg=Team name must be 1-64 characters.",
                                status_code=303)
    if db.query(Team).filter_by(name=name).first() is not None:
        return RedirectResponse("/teams?msg=That team name is taken.", status_code=303)
    team = Team(name=name, captain_id=user.id, invite_code=_new_invite_code(db))
    db.add(team)
    db.commit()
    db.add(TeamMembership(team_id=team.id, user_id=user.id))
    db.commit()
    return RedirectResponse("/teams?msg=Team created — share the join code with your team.",
                            status_code=303)


@router.post("/join")
def join_team(
    request: Request,
    code: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
):
    _require_teams_mode(db)
    if player_team(db, user.id) is not None:
        return RedirectResponse("/teams?msg=You are already in a team.", status_code=303)
    team = db.query(Team).filter_by(invite_code=code.strip().upper()).first()
    if team is None:
        return RedirectResponse("/teams?msg=Invalid join code.", status_code=303)    # Max 4 members — the 5th join attempt is refused.
    if _member_count(db, team.id) >= TEAM_MAX:
        return RedirectResponse(f"/teams?msg={team.name} is full (max {TEAM_MAX}).",
                                status_code=303)
    db.add(TeamMembership(team_id=team.id, user_id=user.id))
    db.commit()
    return RedirectResponse(f"/teams?msg=Welcome to {team.name}!", status_code=303)


@router.post("/leave")
def leave_team(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_csrf),
):
    _require_teams_mode(db)
    team = player_team(db, user.id)
    if team is None:
        raise HTTPException(status_code=404)
    membership = (
        db.query(TeamMembership).filter_by(team_id=team.id, user_id=user.id).first()
    )
    if membership is None:
        raise HTTPException(status_code=404)
    db.query(TeamMembership).filter(
        TeamMembership.team_id == team.id, TeamMembership.user_id == user.id
    ).delete()
    db.commit()
    members = (
        db.query(TeamMembership).filter_by(team_id=team.id)
        .order_by(TeamMembership.joined_at)
        .all()
    )
    if not members:
        # Nobody left — the team dies with its last member.
        db.query(Team).filter(Team.id == team.id).delete()
        db.commit()
        return RedirectResponse("/teams?msg=You left — the team was dissolved.",
                                status_code=303)
    if team.captain_id == user.id:
        # Captainship passes to the longest-standing member.
        team.captain_id = members[0].user_id
        db.commit()
    return RedirectResponse("/teams?msg=You left the team.", status_code=303)
