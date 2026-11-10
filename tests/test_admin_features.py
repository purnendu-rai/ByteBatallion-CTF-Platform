"""Admin features: delete an uploaded file, delete a user, banned users
invisible on the board, reject/recover a solve, per-challenge attempt
limits, and bulk publish. No version bump — behaviour only."""

import re
import shutil

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from app.database import SessionLocal, engine, init_db
from app.flag_engine import DATA_DIR, list_original_files, sha256_hex
from app.main import app
from app.models import Challenge, Solve, Submission, User
from app.routes.scoreboard import ranked_players
from app.security import (
    hash_password,
    login_rate_limiter,
    submission_rate_limiter,
)

FLAG = "BB{test_flag_123}"
CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture(autouse=True)
def _isolate():
    login_rate_limiter.reset_all()
    submission_rate_limiter.reset_all()
    db = SessionLocal()
    try:
        for model in (Solve, Submission, Challenge, User):
            db.query(model).delete()
        db.commit()
    finally:
        db.close()
    yield
    for sub in ("challenges", "instances"):
        base = DATA_DIR / sub
        if base.is_dir():
            for p in base.iterdir():
                if p.is_dir():
                    shutil.rmtree(p)


def get_csrf(client: TestClient, path: str) -> str:
    resp = client.get(path, follow_redirects=True)
    match = CSRF_RE.search(resp.text)
    assert match, f"csrf token not found on {path}"
    return match.group(1)


def register(client: TestClient, username: str):
    r = client.post(
        "/auth/register",
        data={"csrf_token": get_csrf(client, "/auth/login"), "username": username,
              "email": f"{username}@upsifs.in", "password": "password123"},
        follow_redirects=False,
    )
    assert r.status_code == 303


def login(client: TestClient, username: str, password: str = "password123"):
    r = client.post(
        "/auth/login",
        data={"csrf_token": get_csrf(client, "/auth/login"),
              "username": username, "password": password},
        follow_redirects=False,
    )
    assert r.status_code == 303


def make_db_user(db, username: str, role: str = "player") -> User:
    user = User(username=username, email=f"{username}@upsifs.in",
                password_hash=hash_password("password123"), role=role)
    db.add(user)
    db.commit()
    return user


def make_challenge(db, *, title="Web One", category="web", points=100,
                   max_attempts=None, published=True) -> Challenge:
    admin = db.query(User).filter_by(username="admin1").first()
    if admin is None:
        admin = make_db_user(db, "admin1", role="admin")
    ch = Challenge(title=title, category=category, description="test",
                   points=points, author_id=admin.id, flag_template="",
                   flag_sha256=sha256_hex(FLAG), hints=[],
                   is_published=published, max_attempts=max_attempts)
    db.add(ch)
    db.commit()
    return ch


def make_admin_client(username="admin1") -> TestClient:
    db = SessionLocal()
    try:
        if db.query(User).filter_by(username=username).first() is None:
            make_db_user(db, username, role="admin")
    finally:
        db.close()
    client = TestClient(app)
    login(client, username)
    return client


def make_player_client(username: str) -> TestClient:
    client = TestClient(app)
    register(client, username)
    login(client, username)
    return client


def submit(client: TestClient, cid: int, flag: str):
    submission_rate_limiter.reset_all()  # simulate the 5-second gap passing
    return client.post(
        f"/submit/{cid}",
        data={"csrf_token": get_csrf(client, f"/challenges/{cid}"), "flag": flag},
        follow_redirects=False,
    )


def player_id(username: str) -> int:
    db = SessionLocal()
    try:
        return db.query(User).filter_by(username=username).first().id
    finally:
        db.close()


def solve(username: str, cid: int) -> None:
    player = make_player_client(username)
    r = submit(player, cid, FLAG)
    assert r.status_code == 303 and "result=correct" in r.headers["location"]


# --- 1. delete an already-uploaded file from the edit form --------------------

def test_delete_uploaded_file_from_edit():
    admin = make_admin_client()
    r = admin.post(
        "/admin/challenges/new",
        data={"csrf_token": get_csrf(admin, "/admin/challenges/new"),
              "title": "Files Ch", "category": "web", "description": "",
              "points": "100", "max_attempts": "", "hints": "", "flag": FLAG},
        files=[("files", ("note.txt", b"hello", "text/plain"))],
        follow_redirects=False,
    )
    assert r.status_code == 303
    cid = int(r.headers["location"].split("/")[3])
    assert list_original_files(cid) == ["note.txt"]
    assert "note.txt" in admin.get(f"/admin/challenges/{cid}/edit").text

    r = admin.post(
        f"/admin/challenges/{cid}/files/note.txt/delete",
        data={"csrf_token": get_csrf(admin, f"/admin/challenges/{cid}/edit")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert list_original_files(cid) == []
    assert "note.txt" not in admin.get(f"/admin/challenges/{cid}/edit").text
    # Deleting again is a clean 404 (nothing to delete).
    r = admin.post(
        f"/admin/challenges/{cid}/files/note.txt/delete",
        data={"csrf_token": get_csrf(admin, f"/admin/challenges/{cid}/edit")},
        follow_redirects=False,
    )
    assert r.status_code == 404


# --- 2. banned user: invisible on the board (counts + first blood) -----------

def test_banned_user_hidden_from_board_counts_and_first_blood():
    db = SessionLocal()
    try:
        ch = make_challenge(db)
        cid = ch.id
    finally:
        db.close()
    solve("player1", cid)
    admin = make_admin_client()
    board = admin.get("/challenges").text
    assert "1 solves" in board
    assert "first blood: player1" in board
    assert "1 solves" in admin.get(f"/challenges/{cid}").text

    pid = player_id("player1")
    r = admin.post(f"/admin/users/{pid}/ban",
                   data={"csrf_token": get_csrf(admin, "/admin/users")},
                   follow_redirects=False)
    assert r.status_code == 303
    board = admin.get("/challenges").text
    assert "0 solves" in board
    assert "first blood: player1" not in board
    assert "0 solves" in admin.get(f"/challenges/{cid}").text
    # Scoreboard never lists them either.
    assert "player1" not in admin.get("/scoreboard").text


# --- 3. admin can delete a user ----------------------------------------------

def test_admin_can_delete_user():
    db = SessionLocal()
    try:
        ch = make_challenge(db)
        cid = ch.id
    finally:
        db.close()
    solve("player1", cid)
    admin = make_admin_client()
    pid = player_id("player1")
    r = admin.post(f"/admin/users/{pid}/delete",
                   data={"csrf_token": get_csrf(admin, "/admin/users")},
                   follow_redirects=False)
    assert r.status_code == 303
    db = SessionLocal()
    try:
        assert db.get(User, pid) is None
        assert db.query(Solve).filter_by(user_id=pid).count() == 0
    finally:
        db.close()
    assert "player1" not in admin.get("/admin/users").text
    assert "0 solves" in admin.get("/challenges").text
    # Nobody can delete themselves.
    db = SessionLocal()
    try:
        admin_id = db.query(User).filter_by(username="admin1").first().id
    finally:
        db.close()
    r = admin.post(f"/admin/users/{admin_id}/delete",
                   data={"csrf_token": get_csrf(admin, "/admin/users")},
                   follow_redirects=False)
    assert r.status_code == 303
    db = SessionLocal()
    try:
        assert db.get(User, admin_id) is not None
    finally:
        db.close()


# --- 4. reject / recover a solve ----------------------------------------------

def test_reject_and_recover_solve():
    db = SessionLocal()
    try:
        ch = make_challenge(db, points=100)
        cid = ch.id
    finally:
        db.close()
    solve("player1", cid)
    pid = player_id("player1")
    admin = make_admin_client()
    assert "Reject" in admin.get("/admin/submissions?user=player1").text

    r = admin.post(
        "/admin/solves/reject",
        data={"csrf_token": get_csrf(admin, "/admin/submissions"),
              "user_id": pid, "challenge_id": cid,
              "return_to": "/admin/submissions?user=player1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db = SessionLocal()
    try:
        rows = ranked_players(db)
        row = next(x for x in rows if x.username == "player1")
        assert row.points == 0 and row.solve_count == 0
    finally:
        db.close()
    assert "0 solves" in admin.get("/challenges").text
    assert "Recover" in admin.get("/admin/submissions?user=player1").text

    # The player cannot attempt the challenge again while rejected.
    player = TestClient(app)
    login(player, "player1")
    r = submit(player, cid, FLAG)
    assert r.status_code == 303 and "result=rejected" in r.headers["location"]

    # Recover restores the points exactly.
    r = admin.post(
        "/admin/solves/recover",
        data={"csrf_token": get_csrf(admin, "/admin/submissions"),
              "user_id": pid, "challenge_id": cid,
              "return_to": "/admin/submissions?user=player1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db = SessionLocal()
    try:
        rows = ranked_players(db)
        row = next(x for x in rows if x.username == "player1")
        assert row.points == 100 and row.solve_count == 1
    finally:
        db.close()
    assert "1 solves" in admin.get("/challenges").text
    r = submit(player, cid, FLAG)
    assert r.status_code == 303 and "result=already" in r.headers["location"]


# --- 5. per-challenge attempt limits ------------------------------------------

def test_attempts_limit_locks_challenge_and_unlimited_shows_nothing():
    db = SessionLocal()
    try:
        limited = make_challenge(db, title="Limited", max_attempts=2)
        unlimited = make_challenge(db, title="Unlimited", category="crypto",
                                   max_attempts=None)
        lid, uid = limited.id, unlimited.id
    finally:
        db.close()
    player = make_player_client("player1")
    r = submit(player, lid, "BB{wrong_flag}")
    assert r.status_code == 303 and "result=incorrect" in r.headers["location"]
    assert "Attempts remaining: 1" in player.get(f"/challenges/{lid}").text
    r = submit(player, lid, "BB{wrong_flag}")
    assert r.status_code == 303 and "result=incorrect" in r.headers["location"]
    detail = player.get(f"/challenges/{lid}").text
    assert "No attempts left" in detail
    assert "Attempts remaining" not in detail
    r = submit(player, lid, "BB{wrong_flag}")
    assert r.status_code == 303 and "result=locked" in r.headers["location"]
    # The locked attempt was never logged.
    db = SessionLocal()
    try:
        assert db.query(Submission).filter_by(user_id=player_id("player1"),
                                              challenge_id=lid).count() == 2
    finally:
        db.close()
    # Unlimited shows no attempts text at all.
    assert "Attempts remaining" not in player.get(f"/challenges/{uid}").text


def test_max_attempts_dropdown_validation():
    admin = make_admin_client()
    base = {"title": "Attempts Ch", "category": "web", "description": "",
            "points": "100", "hints": "", "flag": FLAG}
    r = admin.post(
        "/admin/challenges/new",
        data={"csrf_token": get_csrf(admin, "/admin/challenges/new"),
              **base, "max_attempts": "99"},
        follow_redirects=False,
    )
    from urllib.parse import unquote

    assert r.status_code == 303
    assert "Attempts must be" in unquote(r.headers["location"])
    r = admin.post(
        "/admin/challenges/new",
        data={"csrf_token": get_csrf(admin, "/admin/challenges/new"),
              **base, "max_attempts": "5"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    cid = int(r.headers["location"].split("/")[3])
    db = SessionLocal()
    try:
        assert db.get(Challenge, cid).max_attempts == 5
    finally:
        db.close()
    # The edit form remembers the choice (5 selected, Unlimited not).
    assert '<option value="5" selected>' in admin.get(f"/admin/challenges/{cid}/edit").text


# --- 6. bulk publish: same timestamp, so they appear together -----------------

def test_bulk_publish_sets_same_timestamp():
    admin = make_admin_client()
    ids = []
    for title in ("Bulk A", "Bulk B"):
        r = admin.post(
            "/admin/challenges/new",
            data={"csrf_token": get_csrf(admin, "/admin/challenges/new"),
                  "title": title, "category": "web", "description": "",
                  "points": "100", "max_attempts": "", "hints": "", "flag": FLAG},
            follow_redirects=False,
        )
        assert r.status_code == 303
        ids.append(int(r.headers["location"].split("/")[3]))
    r = admin.post(
        "/admin/challenges/publish",
        data={"csrf_token": get_csrf(admin, "/admin/challenges"),
              "challenge_ids": [str(i) for i in ids]},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db = SessionLocal()
    try:
        rows = [db.get(Challenge, i) for i in ids]
        assert all(c.is_published for c in rows)
        assert rows[0].created_at == rows[1].created_at
    finally:
        db.close()
    board = admin.get("/challenges").text
    assert "Bulk A" in board and "Bulk B" in board


# --- 7. additive migration adds the new columns to an old database -----------
# (kept LAST: it drops the columns and relies on init_db() to re-add them)

def test_missing_columns_added_by_init_db():
    import sqlite3

    db_path = engine.url.database
    con = sqlite3.connect(db_path)
    try:
        con.execute("ALTER TABLE solves DROP COLUMN is_rejected")
        con.execute("ALTER TABLE challenges DROP COLUMN max_attempts")
        con.commit()
    finally:
        con.close()
    init_db()
    cols = {
        t: {c["name"] for c in inspect(engine).get_columns(t)}
        for t in ("solves", "challenges")
    }
    assert "is_rejected" in cols["solves"]
    assert "max_attempts" in cols["challenges"]


# --- 8. migration tolerates Turso's Hrana-style duplicate error ---------------
# Regression for the 2026-11-10 live deploy failure: the libsql dialect
# raises the duplicate-column failure as ValueError ("Hrana: SQLite
# error: duplicate column name: ..."), NOT OperationalError, so catching
# only OperationalError crashed the boot. The swallow keys on the
# message, so any dialect's duplicate error is tolerated — but a
# different error must still raise.


class _FakeConn:
    def __init__(self, fail_first_with):
        self.calls = 0
        self.fail_first_with = fail_first_with

    def execute(self, stmt):
        self.calls += 1
        if self.calls == 1 and self.fail_first_with is not None:
            raise self.fail_first_with


class _FakeCtx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self, fail_first_with):
        self.conn = _FakeConn(fail_first_with)

    def begin(self):
        return _FakeCtx(self.conn)


def test_migration_tolerates_hrana_style_duplicate_error(monkeypatch):
    import app.database as dbmod

    fake = _FakeEngine(
        ValueError("Hrana: SQLite error: duplicate column name: flag_sha256")
    )
    monkeypatch.setattr(dbmod, "engine", fake)
    dbmod._add_missing_columns()  # must not raise
    assert fake.conn.calls == 3  # all three ALTERs still attempted


def test_migration_reraises_non_duplicate_errors(monkeypatch):
    import app.database as dbmod

    fake = _FakeEngine(
        ValueError("Hrana: SQLite error: no such table: challenges")
    )
    monkeypatch.setattr(dbmod, "engine", fake)
    with pytest.raises(ValueError, match="no such table"):
        dbmod._add_missing_columns()
