"""Board order — upload order, not points or a fixed category list.

Acceptance (user request, no version bump):
- sections run in first-upload order: the category whose FIRST
  challenge was uploaded earliest sits on top — so a category that
  appears later in CHALLENGE_CATEGORIES still comes first if its
  first challenge went up first
- inside a section the oldest challenge is the leftmost card and the
  newest is the rightmost
"""

import re
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.flag_engine import sha256_hex
from app.main import app
from app.models import Challenge, User
from app.security import hash_password, login_rate_limiter

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture(autouse=True)
def _isolate():
    login_rate_limiter.reset_all()
    db = SessionLocal()
    try:
        db.query(Challenge).delete()
        db.query(User).delete()
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(Challenge).delete()
        db.query(User).delete()
        db.commit()
    finally:
        db.close()


def _user(username: str, role: str = "player"):
    db = SessionLocal()
    try:
        db.add(User(username=username, email=f"{username}@upsifs.in",
                    password_hash=hash_password("password123"), role=role))
        db.commit()
    finally:
        db.close()


def _challenge(db, *, title: str, category: str, created_at: datetime,
               points: int = 100):
    admin = db.query(User).filter_by(role="admin").first()
    ch = Challenge(title=title, category=category, description="order test",
                   points=points, author_id=admin.id, flag_template="",
                   flag_sha256=sha256_hex("BB{test_flag_value}"),
                   hints=[], is_published=True, created_at=created_at)
    db.add(ch)
    db.commit()
    return ch


def _board() -> str:
    _user("admin1", role="admin")
    db = SessionLocal()
    try:
        # Deliberately NOT in fixed-list order and NOT in points order:
        # crypto's first challenge is the oldest, web's first is newer,
        # and the high-points web card is older than the low-points one.
        _challenge(db, title="Crypto Old", category="crypto",
                   created_at=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
                   points=500)
        _challenge(db, title="Crypto New", category="crypto",
                   created_at=datetime(2026, 1, 3, 10, 0, tzinfo=timezone.utc),
                   points=100)
        _challenge(db, title="Web Old", category="web",
                   created_at=datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc),
                   points=300)
        _challenge(db, title="Web New", category="web",
                   created_at=datetime(2026, 1, 4, 10, 0, tzinfo=timezone.utc),
                   points=50)
    finally:
        db.close()
    player = TestClient(app)
    _user("player1")
    r = player.get("/auth/login")
    token = CSRF_RE.search(r.text).group(1)
    player.post("/auth/login",
                data={"csrf_token": token, "username": "player1",
                      "password": "password123"}, follow_redirects=False)
    return player.get("/challenges").text


def test_sections_run_in_first_upload_order():
    board = _board()
    # crypto's first challenge (1 Jan) predates web's (2 Jan), so the
    # crypto section sits above web — even though "web" is first in
    # the old fixed CHALLENGE_CATEGORIES list.
    assert board.index("Crypto Old") < board.index("Web Old")
    assert board.index(">crypto<") < board.index(">web<")


def test_cards_inside_a_section_run_oldest_first():
    board = _board()
    # Oldest card leftmost, newest rightmost, in both sections —
    # regardless of points (Crypto Old is 500 pts, Crypto New 100).
    assert board.index("Crypto Old") < board.index("Crypto New")
    assert board.index("Web Old") < board.index("Web New")
