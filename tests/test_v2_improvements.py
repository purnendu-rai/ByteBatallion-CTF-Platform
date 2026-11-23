"""v2 improvement tests: solved cards on the board, custom categories in
the profile graph, BB{...} flag format, login guards + `next`, the PDF
submission export, the Notifications page, and the new home page."""

import re
import shutil
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.flag_engine import DATA_DIR, sha256_hex
from app.main import app
from app.models import (
    Announcement,
    Challenge,
    CheatLog,
    ConfigEntry,
    Instance,
    Solve,
    Submission,
    User,
)
from app.security import hash_password, login_rate_limiter, submission_rate_limiter

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
FLAG = "BB{test_flag_value}"


@pytest.fixture(autouse=True)
def _isolate():
    login_rate_limiter.reset_all()
    submission_rate_limiter.reset_all()
    db = SessionLocal()
    try:
        db.query(Solve).delete()
        db.query(Submission).delete()
        db.query(CheatLog).delete()
        db.query(Instance).delete()
        db.query(Announcement).delete()
        db.query(ConfigEntry).delete()
        db.query(Challenge).delete()
        db.query(User).delete()
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


def get_csrf(client: TestClient, path: str = "/auth/login") -> str:
    resp = client.get(path, follow_redirects=True)
    match = CSRF_RE.search(resp.text)
    assert match, f"csrf token not found on {path}"
    return match.group(1)


def register(client: TestClient, username: str):
    r = client.post(
        "/auth/register",
        data={"csrf_token": get_csrf(client), "username": username,
              "email": f"{username}@upsifs.in", "password": "password123"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    return r


def login(client: TestClient, username: str, password: str = "password123"):
    r = client.post(
        "/auth/login",
        data={"csrf_token": get_csrf(client), "username": username, "password": password},
        follow_redirects=False,
    )
    assert r.status_code == 303
    return r


def make_admin(username: str = "admin1") -> None:
    db = SessionLocal()
    try:
        db.add(User(username=username, email=f"{username}@upsifs.in",
                    password_hash=hash_password("password123"), role="admin"))
        db.commit()
    finally:
        db.close()


def make_challenge(title: str, category: str = "web", flag: str = FLAG) -> int:
    db = SessionLocal()
    try:
        admin = db.query(User).filter_by(role="admin").first()
        ch = Challenge(title=title, category=category, description="desc",
                       points=100, author_id=admin.id, flag_template="",
                       flag_sha256=sha256_hex(flag), hints=[],
                       is_published=True)
        db.add(ch)
        db.commit()
        return ch.id
    finally:
        db.close()


def make_admin_client(username: str = "admin1") -> TestClient:
    make_admin(username)
    client = TestClient(app)
    login(client, username)
    return client


def solve(client: TestClient, cid: int, flag: str = FLAG):
    return client.post(
        f"/submit/{cid}",
        data={"csrf_token": get_csrf(client, f"/challenges/{cid}"), "flag": flag},
        follow_redirects=False,
    )


# --- (a) solved challenges are green on the board ------------------------------

def test_board_marks_solved_challenges_green():
    make_admin()
    solved_id = make_challenge("Solved One")
    unsolved_id = make_challenge("Unsolved One")
    player = TestClient(app)
    register(player, "alice")
    r = solve(player, solved_id)
    assert r.status_code == 303
    assert "result=correct" in r.headers["location"]

    board = player.get("/challenges").text
    assert board.count("chal-card solved") == 1
    assert "[SOLVED]" in board
    # The solved card carries the badge + class; the unsolved one has neither.
    solved_pos = board.index("Solved One")
    unsolved_pos = board.index("Unsolved One")
    assert "chal-card solved" in board[max(0, solved_pos - 200):solved_pos + 200]
    assert "[SOLVED]" in board[solved_pos:solved_pos + 200]
    assert "chal-card solved" not in board[max(0, unsolved_pos - 200):unsolved_pos + 200]
    assert "[SOLVED]" not in board[unsolved_pos:unsolved_pos + 200]


# --- (b) custom categories appear in the profile graph -------------------------

def test_custom_category_shows_in_profile_graph():
    make_admin()
    cid = make_challenge("Hardware Hack", category="hardware")
    player = TestClient(app)
    register(player, "bob")
    solve(player, cid)

    profile = player.get("/profile").text
    # Donut segment for the custom category + legend row with count + pct.
    assert 'data-cat="hardware"' in profile
    assert "Hardware — 1 (100.0%)" in profile
    # The generic badge style covers the arbitrary category in the log.
    assert "badge-cat-hardware" in profile


# --- (c) flag examples use BB{...} ----------------------------------------------

def test_flag_examples_use_bb_prefix():
    admin = make_admin_client()
    form = admin.get("/admin/challenges/new").text
    assert "BB{y0u_f0und_1t}" in form
    assert "BYTEBATTALION{" not in form

    make_admin("admin2")
    cid = make_challenge("Flag Format Check")
    player = TestClient(app)
    register(player, "carol")
    detail = player.get(f"/challenges/{cid}").text
    assert 'placeholder="BB{...}"' in detail
    assert "BYTEBATTALION{" not in detail

    # The engine itself stays generic — any PREFIX{...} validates.
    from app.flag_engine import validate_challenge_flag
    assert validate_challenge_flag("BB{something}")[0]
    assert validate_challenge_flag("BB{test_flag_value}")[0]


# --- (d) anonymous users are sent to login, with `next` -------------------------

@pytest.mark.parametrize("path", [
    "/challenges", "/profile", "/scoreboard", "/notifications",
    "/admin/submissions",
])
def test_anonymous_pages_redirect_to_login_with_next(path):
    anon = TestClient(app)
    r = anon.get(path, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/auth/login?next=")
    # The `next` value is URL-encoded in the Location header.
    assert quote(path, safe="") in r.headers["location"]


def test_anonymous_top10_json_redirects_to_login():
    anon = TestClient(app)
    r = anon.get("/scoreboard/top10.json", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/auth/login?next=")


# --- (e) login honours `next` ----------------------------------------------------

def test_login_post_with_next_query_param_redirects_there():
    player = TestClient(app)
    register(player, "dave")
    client = TestClient(app)
    r = client.post(
        "/auth/login?next=/challenges",
        data={"csrf_token": get_csrf(client), "username": "dave",
              "password": "password123"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/challenges"


def test_login_post_with_next_hidden_field_redirects_there():
    player = TestClient(app)
    register(player, "erin")
    client = TestClient(app)
    r = client.post(
        "/auth/login",
        data={"csrf_token": get_csrf(client), "username": "erin",
              "password": "password123", "next": "/scoreboard"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/scoreboard"


def test_login_post_with_unsafe_next_falls_back_to_challenges():
    player = TestClient(app)
    register(player, "frank")
    client = TestClient(app)
    r = client.post(
        "/auth/login",
        data={"csrf_token": get_csrf(client), "username": "frank",
              "password": "password123", "next": "https://evil.example/x"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/challenges"


# --- (f) submissions PDF export --------------------------------------------------

def test_submissions_pdf_export():
    admin = make_admin_client()
    cid = make_challenge("PDF Chal")
    player = TestClient(app)
    register(player, "grace")
    solve(player, cid)
    # 1 submission per 5s per user — reset the limiter for the second attempt.
    submission_rate_limiter.reset_all()
    solve(player, cid, "BB{wrong_answer}")

    r = admin.get("/admin/submissions/export.pdf", follow_redirects=False)
    assert r.status_code == 200
    assert "application/pdf" in r.headers["content-type"]
    assert r.content[:5] == b"%PDF-"
    assert "bytebatallion-submissions.pdf" in r.headers["content-disposition"]
    # Hash prefixes only — no plaintext flag anywhere in the PDF.
    assert b"BB{test_flag_value}" not in r.content
    assert b"BB{wrong_answer}" not in r.content

    # A logged-in non-admin gets 403; anonymous gets the login redirect.
    r = player.get("/admin/submissions/export.pdf", follow_redirects=False)
    assert r.status_code == 403
    anon = TestClient(app)
    r = anon.get("/admin/submissions/export.pdf", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/auth/login?next=")


# --- (g) notifications page + unread JSON ----------------------------------------

def test_notifications_page_and_unread_json():
    admin = make_admin_client()
    db = SessionLocal()
    try:
        admin_user = db.query(User).filter_by(role="admin").first()
        from app.models import utc_now
        db.add(Announcement(title="Welcome", body="Event on Friday.",
                            created_by=admin_user.id, created_at=utc_now()))
        db.commit()
    finally:
        db.close()

    player = TestClient(app)
    register(player, "heidi")
    # Home no longer contains any announcement.
    assert "Welcome" not in player.get("/").text
    assert "Welcome" not in player.get("/challenges").text
    # The Notifications page does.
    page = player.get("/notifications")
    assert page.status_code == 200
    assert "Welcome" in page.text and "Event on Friday." in page.text
    # Unread endpoint: 1 unread before seeing, 0 after.
    data = player.get("/notifications/unread.json?seen=0").json()
    assert data["unread"] == 1
    assert data["latest"]["title"] == "Welcome"
    assert data["latest_id"] == data["latest"]["id"]
    data = player.get(f"/notifications/unread.json?seen={data['latest_id']}").json()
    assert data["unread"] == 0
    # Non-int `seen` is tolerated.
    assert player.get("/notifications/unread.json?seen=nope").json()["unread"] == 1


def test_notifications_empty_state():
    player = TestClient(app)
    register(player, "ivan")
    page = player.get("/notifications").text
    assert "No notifications yet." in page
    data = player.get("/notifications/unread.json").json()
    assert data == {"unread": 0, "latest": None, "latest_id": 0}


# --- home page: terminal, tagline, logo, footer -----------------------------------

def test_home_page_terminal_tagline_logo_footer():
    player = TestClient(app)
    register(player, "judy")
    home = player.get("/").text
    assert "Be smart enough to get the flag" in home
    # v2.3: the "// no shortcuts" tagline was removed from the home page.
    assert "no shortcuts. no handouts. just you vs. the box." not in home
    assert "Coming soon" not in home
    assert "every player gets their own flag" not in home
    assert 'id="terminal"' in home
    assert 'data-username="judy"' in home
    assert "/static/img/logo.png" in home
    assert "Made by HackerP" in home
    assert "per-user flags: ENABLED" not in home


def test_home_page_uses_guest_prompt_when_anonymous():
    anon = TestClient(app)
    home = anon.get("/").text
    assert 'data-username="guest"' in home
    assert "Be smart enough to get the flag" in home


def test_logged_in_html_responses_are_no_store():
    player = TestClient(app)
    register(player, "kyle")
    r = player.get("/challenges", follow_redirects=False)
    assert "no-store" in r.headers.get("cache-control", "")
    # v3.6: logged-OUT pages are no-store too — a cached page must
    # never be shown to the next person on a shared computer as if
    # they were still signed in.
    anon = TestClient(app)
    r = anon.get("/", follow_redirects=False)
    assert "no-store" in r.headers.get("cache-control", "")
    assert "cookie" in r.headers.get("vary", "").lower()
    # JSON APIs are left alone.
    r = player.get("/scoreboard/top10.json", follow_redirects=False)
    assert "no-store" not in r.headers.get("cache-control", "")
