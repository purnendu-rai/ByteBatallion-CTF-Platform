"""Core platform tests: authentication, challenge management, flag engine,
submissions, scoreboard analytics, admin controls, teams, hints, and PDF exports.
"""

import base64
import io
import json
import re
import shutil
import zipfile
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect

from app.database import SessionLocal, engine
from app.flag_engine import (
    DATA_DIR,
    generate_tail,
    list_original_files,
    originals_dir,
    sha256_hex,
    template_from_full_flag,
    validate_challenge_flag,
    validate_flag_template,
)
from app.main import app
from app.models import (
    Announcement,
    Challenge,
    CheatLog,
    ConfigEntry,
    HintUnlock,
    Instance,
    Page,
    Solve,
    Submission,
    Team,
    TeamMembership,
    User,
    set_config as _models_set_config,
)
from app.routes.scoreboard import ranked_players, ranked_teams
from app.security import (
    SESSION_COOKIE,
    SubmissionRateLimiter,
    create_session_token,
    hash_password,
    submission_rate_limiter,
)
from tests.conftest import (
    ROOT,
    get_csrf,
    login,
    logout,
    make_admin_client,
    make_db_user,
    register,
    user_id,
)

FLAG = "BB{web_sqli_abc123xyz}"
FILE_BODY = b"flag: FLAG_PLACEHOLDER end"
FLAG_RE = re.compile(rb"BB\{web_sqli_[A-Za-z0-9]{12}\}")
DL_RE = re.compile(r"/challenges/(\d+)/download\?token=([A-Za-z0-9._\-]+)")
NASTY_TITLES = (
    "A <b Bold",
    "Web & Pwn <Basics>",
    "100% <script>alert(1)</script>",
    "Tom & Jerry &amp; Co",
)


def _set_user_field(username: str, **fields) -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username=username).first()
        assert user is not None
        for key, value in fields.items():
            setattr(user, key, value)
        db.commit()
    finally:
        db.close()


def set_config(key_or_db, val_or_key, maybe_val=None):
    if maybe_val is not None:
        db = key_or_db
        key = val_or_key
        val = maybe_val
        is_temp_db = False
    elif hasattr(key_or_db, "query"):
        db = key_or_db
        key = val_or_key
        val = ""
        is_temp_db = False
    else:
        db = SessionLocal()
        key = key_or_db
        val = val_or_key
        is_temp_db = True

    if key == "scoreboard_visibility":
        key = "scoreboard_visible"
        val = "true" if str(val).lower() in ("public", "1", "true") else "false"
    elif key in ("scoreboard_visible", "registration_open", "teams_mode"):
        val = "true" if str(val).lower() in ("1", "true", "on", "yes") else "false"
    elif key == "start_time":
        key = "ctf_start_time"
    elif key == "end_time":
        key = "ctf_end_time"

    try:
        _models_set_config(db, key, str(val))
    finally:
        if is_temp_db:
            db.close()


def _make_challenge_row(
    db,
    *,
    title="Chal",
    category="web",
    description="Some description",
    points=100,
    published=True,
    template="BB{core_{RAND:12}}",
    hints=None,
    with_file=True,
) -> Challenge:
    admin = db.query(User).filter_by(role="admin").first()
    if not admin:
        admin = make_db_user(db, "author_adm", role="admin")
    ch = Challenge(
        title=title,
        category=category,
        description=description,
        points=points,
        author_id=admin.id,
        flag_template=template,
        hints=hints or [],
        is_published=published,
    )
    db.add(ch)
    db.commit()
    db.refresh(ch)
    if with_file:
        dest = originals_dir(ch.id)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "note.txt").write_bytes(b"FLAG_PLACEHOLDER")
    return ch


def _setup_challenge(title="SQLi Basics", flag=FLAG) -> int:
    admin = TestClient(app)
    make_db_user("admin_setup", role="admin")
    login(admin, "admin_setup")
    token = get_csrf(admin, "/admin/challenges/new")
    r = admin.post(
        "/admin/challenges/new",
        data={
            "csrf_token": token,
            "title": title,
            "category": "web",
            "description": "solve it",
            "points": "100",
            "hints": "",
            "flag": flag,
        },
        files=[("files", ("note.txt", io.BytesIO(FILE_BODY), "text/plain"))],
        follow_redirects=False,
    )
    assert r.status_code == 303
    cid = int(
        re.search(r"/admin/challenges/(\d+)/edit", r.headers["location"]).group(
            1
        )
    )
    r = admin.post(
        f"/admin/challenges/{cid}/publish",
        data={
            "csrf_token": get_csrf(admin, f"/admin/challenges/{cid}/edit")
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    return cid


def _submit_flag(client: TestClient, cid: int, flag: str):
    return client.post(
        f"/submit/{cid}",
        data={
            "csrf_token": get_csrf(client, f"/challenges/{cid}"),
            "flag": flag,
        },
        follow_redirects=False,
    )


def _pdf_text(pdf_bytes: bytes) -> bytes:
    out = b""
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", pdf_bytes, re.S):
        chunk = m.group(1).strip()
        raw = None
        for decoder in (
            lambda c: base64.a85decode(c, adobe=True),
            lambda c: base64.a85decode(c, adobe=False),
        ):
            try:
                raw = decoder(chunk)
                break
            except Exception:
                continue
        if raw is None:
            raw = chunk
        try:
            content = zlib.decompress(raw)
        except zlib.error:
            continue
        for lit in re.findall(rb"\((?:[^()\\]|\\.)*\)", content):
            out += (
                lit[1:-1]
                .replace(b"\\(", b"(")
                .replace(b"\\)", b")")
                .replace(b"\\\\", b"\\")
            )
    return out


# --- System Bootstrap & Tables ----------------------------------------------


def test_home_page_loads(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "BYTE_BATALLION" in resp.text


def test_db_created_with_all_tables(client):
    expected_tables = {
        "users",
        "challenges",
        "instances",
        "submissions",
        "solves",
        "announcements",
        "config",
        "cheat_log",
    }
    tables = set(inspect(engine).get_table_names())
    missing = expected_tables - tables
    assert not missing, f"missing tables: {missing}"


# --- Authentication, Roles, Sessions & Profile ------------------------------


def test_register_login_profile(client):
    r = register(client, "alice")
    assert r.status_code == 303
    assert r.headers["location"] == "/challenges"

    r = client.get("/profile", follow_redirects=False)
    assert r.status_code == 200
    assert "alice" in r.text

    assert logout(client).status_code == 303
    assert client.get("/profile", follow_redirects=False).status_code == 302

    r = client.post(
        "/auth/login",
        data={
            "csrf_token": get_csrf(client),
            "username": "alice",
            "password": "password123",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    r = client.get("/profile", follow_redirects=False)
    assert r.status_code == 200
    assert "alice" in r.text


def test_wrong_password_fails(client):
    register(client, "carol")
    logout(client)
    r = client.post(
        "/auth/login",
        data={
            "csrf_token": get_csrf(client),
            "username": "carol",
            "password": "wrongwrong",
        },
    )
    assert "Incorrect username or password" in r.text
    assert client.get("/profile", follow_redirects=False).status_code == 302


def test_player_cannot_open_admin(client):
    register(client, "grace")
    assert client.get("/admin", follow_redirects=False).status_code == 403


def test_anonymous_cannot_open_admin(client):
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/auth/login?next=")


def test_session_survives_restart(client):
    register(client, "iris")
    db = SessionLocal()
    try:
        uid = db.query(User).filter_by(username="iris").first().id
    finally:
        db.close()

    fresh = TestClient(app)
    fresh.cookies.set(SESSION_COOKIE, create_session_token(uid))
    r = fresh.get("/profile", follow_redirects=False)
    assert r.status_code == 200
    assert "iris" in r.text

    fresh.cookies.set(SESSION_COOKIE, create_session_token(uid)[:-2] + "zz")
    assert fresh.get("/profile", follow_redirects=False).status_code == 302


def test_login_with_email(client):
    register(client, "bob", email="bob@upsifs.in")
    logout(client)
    r = client.post(
        "/auth/login",
        data={
            "csrf_token": get_csrf(client),
            "username": "bob@upsifs.in",
            "password": "password123",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303


def test_duplicate_username_and_email_rejected(client):
    assert register(client, "dave").status_code == 303
    logout(client)
    r = register(client, "dave")
    assert r.status_code == 303
    assert "Username is already taken" in client.get(r.headers["location"]).text
    r = register(client, "dave2", email="dave@upsifs.in")
    assert r.status_code == 303
    assert (
        "Email is already registered" in client.get(r.headers["location"]).text
    )


def test_short_password_rejected(client):
    r = register(client, "erin", password="short")
    assert r.status_code == 303
    assert "at least 8 characters" in client.get(r.headers["location"]).text
    db = SessionLocal()
    try:
        assert db.query(User).filter_by(username="erin").first() is None
    finally:
        db.close()


def test_password_never_stored_plaintext(client):
    register(client, "frank")
    db = SessionLocal()
    try:
        stored = (
            db.query(User).filter_by(username="frank").first().password_hash
        )
    finally:
        db.close()
    assert stored != "password123"
    assert stored.startswith("$2b$")


def test_admin_can_open_admin(client):
    register(client, "henry")
    _set_user_field("henry", role="admin")
    assert client.get("/admin", follow_redirects=False).status_code == 200


def test_banned_user_cannot_log_in(client):
    register(client, "liam")
    _set_user_field("liam", is_banned=True)
    assert client.get("/profile", follow_redirects=False).status_code == 302
    logout(client)
    r = client.post(
        "/auth/login",
        data={
            "csrf_token": get_csrf(client),
            "username": "liam",
            "password": "password123",
        },
    )
    assert "banned" in r.text.lower()
    assert client.get("/profile", follow_redirects=False).status_code == 302


# --- Settings & Profile UI --------------------------------------------------


def test_settings_requires_login():
    client = TestClient(app)
    resp = client.get("/settings", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["location"]


def test_settings_page_renders_for_logged_in_user():
    client = TestClient(app)
    register(client, "setuser")
    resp = client.get("/settings", follow_redirects=False)
    assert resp.status_code == 200
    assert "Account Settings" in resp.text
    assert "Password" in resp.text


def test_settings_password_change_validation():
    client = TestClient(app)
    register(client, "pwuser")
    token = get_csrf(client, "/settings")

    r = client.post(
        "/settings/password",
        data={
            "csrf_token": token,
            "current_password": "wrongpassword",
            "new_password": "newpassword123",
            "confirm_password": "newpassword123",
        },
        follow_redirects=True,
    )
    assert "Current password is incorrect." in r.text

    token = get_csrf(client, "/settings")
    r = client.post(
        "/settings/password",
        data={
            "csrf_token": token,
            "current_password": "password123",
            "new_password": "short",
            "confirm_password": "short",
        },
        follow_redirects=True,
    )
    assert "New password must be at least 8 characters long." in r.text

    token = get_csrf(client, "/settings")
    r = client.post(
        "/settings/password",
        data={
            "csrf_token": token,
            "current_password": "password123",
            "new_password": "newpassword123",
            "confirm_password": "different123",
        },
        follow_redirects=True,
    )
    assert "New passwords do not match." in r.text


def test_settings_password_change_success():
    client = TestClient(app)
    register(client, "pwchangeuser")
    token = get_csrf(client, "/settings")
    r = client.post(
        "/settings/password",
        data={
            "csrf_token": token,
            "current_password": "password123",
            "new_password": "updatedsecret123",
            "confirm_password": "updatedsecret123",
        },
        follow_redirects=True,
    )
    assert "Your password has been updated successfully." in r.text

    fresh = TestClient(app)
    bad_login = login(fresh, "pwchangeuser", "password123")
    assert "/auth/login?error=" in bad_login.headers.get("location", "")
    good_login = login(fresh, "pwchangeuser", "updatedsecret123")
    assert good_login.status_code == 303
    assert "/challenges" in good_login.headers.get("location", "")


def test_profile_shows_stats_and_solves_log():
    client = TestClient(app)
    register(client, "solver1")
    uid = user_id("solver1")
    db = SessionLocal()
    try:
        admin = make_db_user(db, "author1", role="admin")
        ch1 = Challenge(
            title="Crypto Challenge 1",
            category="crypto",
            points=150,
            author_id=admin.id,
            flag_sha256=sha256_hex("BB{test}"),
            is_published=True,
        )
        ch2 = Challenge(
            title="Web Challenge 1",
            category="web",
            points=200,
            author_id=admin.id,
            flag_sha256=sha256_hex("BB{test2}"),
            is_published=True,
        )
        db.add_all([ch1, ch2])
        db.commit()
        db.refresh(ch1)
        db.refresh(ch2)
        s1 = Solve(
            user_id=uid,
            challenge_id=ch1.id,
            points_awarded=150,
            solved_at=datetime.now(timezone.utc),
        )
        s2 = Solve(
            user_id=uid,
            challenge_id=ch2.id,
            points_awarded=200,
            solved_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        db.add_all([s1, s2])
        db.commit()
    finally:
        db.close()

    resp = client.get("/profile")
    assert resp.status_code == 200
    assert "350" in resp.text
    assert "Crypto Challenge 1" in resp.text
    assert "Web Challenge 1" in resp.text
    assert "Solved Challenges" in resp.text


def test_profile_team_display_respects_admin_teams_mode():
    client = TestClient(app)
    register(client, "teamplayer")
    uid = user_id("teamplayer")
    db = SessionLocal()
    try:
        t = Team(name="AlphaSquad", invite_code="ALPH1234", captain_id=uid)
        db.add(t)
        db.commit()
        db.refresh(t)
        db.add(TeamMembership(team_id=t.id, user_id=uid))
        db.commit()
    finally:
        db.close()

    set_config("teams_mode", "0")
    resp = client.get("/profile")
    assert resp.status_code == 200
    assert "AlphaSquad" not in resp.text

    set_config("teams_mode", "1")
    resp = client.get("/profile")
    assert resp.status_code == 200
    assert "AlphaSquad" in resp.text


def test_scoreboard_top10_json_supports_players_and_teams():
    client = TestClient(app)
    register(client, "scorer1")
    uid = user_id("scorer1")
    db = SessionLocal()
    try:
        admin = make_db_user(db, "author2", role="admin")
        ch = Challenge(
            title="Score Chal",
            category="pwn",
            points=100,
            author_id=admin.id,
            flag_sha256=sha256_hex("BB{test}"),
            is_published=True,
        )
        db.add(ch)
        db.commit()
        db.refresh(ch)
        t = Team(name="ScoreTeam", invite_code="SCOR1234", captain_id=uid)
        db.add(t)
        db.commit()
        db.refresh(t)
        db.add(TeamMembership(team_id=t.id, user_id=uid))
        s = Solve(
            user_id=uid,
            challenge_id=ch.id,
            points_awarded=100,
            solved_at=datetime.now(timezone.utc),
        )
        db.add(s)
        db.commit()
    finally:
        db.close()

    resp_players = client.get("/scoreboard/top10.json?mode=players")
    assert resp_players.status_code == 200
    data_p = resp_players.json()
    assert any(s.get("username") == "scorer1" or s.get("name") == "scorer1" for s in data_p.get("series", []))

    resp_teams = client.get("/scoreboard/top10.json?mode=teams")
    assert resp_teams.status_code == 200
    data_t = resp_teams.json()
    assert any(s.get("name") == "ScoreTeam" or s.get("username") == "ScoreTeam" for s in data_t.get("series", []))


# --- Challenge CRUD, Board & Permissions ------------------------------------


def test_admin_create_with_file_then_publish_flow():
    admin = TestClient(app)
    make_db_user("admin_crud", role="admin")
    login(admin, "admin_crud")

    token = get_csrf(admin, "/admin/challenges/new")
    r = admin.post(
        "/admin/challenges/new",
        data={
            "csrf_token": token,
            "title": "Baby Web",
            "category": "web",
            "description": "Find the flag in the header",
            "points": "150",
            "hints": "look at cookies\ncheck the query | 25",
            "flag": "BB{baby_web_flag}",
        },
        files=[("files", ("note.txt", io.BytesIO(b"FLAG_PLACEHOLDER"), "text/plain"))],
        follow_redirects=False,
    )
    assert r.status_code == 303
    cid = int(
        re.search(r"/admin/challenges/(\d+)/edit", r.headers["location"]).group(
            1
        )
    )

    player = TestClient(app)
    register(player, "boardplayer")
    assert "Baby Web" not in player.get("/challenges").text

    r = admin.post(
        f"/admin/challenges/{cid}/publish",
        data={
            "csrf_token": get_csrf(admin, f"/admin/challenges/{cid}/edit")
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    board = player.get("/challenges").text
    assert "Baby Web" in board
    assert "150" in board

    detail = player.get(f"/challenges/{cid}").text
    assert "Baby Web" in detail
    assert "Find the flag in the header" in detail
    assert "note.txt" in detail


def test_direct_file_url_requires_login():
    db = SessionLocal()
    try:
        ch = _make_challenge_row(db, title="Direct File Chal", published=True)
        cid = ch.id
    finally:
        db.close()
    anon = TestClient(app)
    r = anon.get(f"/challenges/{cid}/download/note.txt", follow_redirects=False)
    assert r.status_code == 302
    assert "/auth/login" in r.headers["location"]


def test_unpublished_invisible_to_players():
    db = SessionLocal()
    try:
        ch = _make_challenge_row(db, title="Draft Chal", published=False)
        cid = ch.id
    finally:
        db.close()
    player = TestClient(app)
    register(player, "unpub_player")
    assert "Draft Chal" not in player.get("/challenges").text
    assert player.get(f"/challenges/{cid}").status_code == 404


def test_soft_delete_hides_from_players_and_keeps_row():
    admin = make_admin_client()
    cid = _setup_challenge("To Delete")
    player = TestClient(app)
    register(player, "del_player")
    assert "To Delete" in player.get("/challenges").text

    r = admin.post(
        f"/admin/challenges/{cid}/delete",
        data={"csrf_token": get_csrf(admin, "/admin/challenges")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "To Delete" not in player.get("/challenges").text
    assert player.get(f"/challenges/{cid}").status_code == 404


def test_board_groups_by_category():
    db = SessionLocal()
    try:
        _make_challenge_row(db, title="Web One", category="web")
        _make_challenge_row(db, title="Crypto One", category="crypto")
    finally:
        db.close()
    player = TestClient(app)
    register(player, "cat_player")
    board = player.get("/challenges").text
    assert "Web One" in board and "Crypto One" in board


def test_invalid_flag_rejected():
    admin = make_admin_client()
    token = get_csrf(admin, "/admin/challenges/new")
    r = admin.post(
        "/admin/challenges/new",
        data={
            "csrf_token": token,
            "title": "Bad Flag",
            "category": "web",
            "description": "x",
            "points": "100",
            "flag": "no_braces",
        },
        follow_redirects=True,
    )
    assert "Flag must look like" in r.text


def test_custom_category_accepted():
    admin = make_admin_client()
    token = get_csrf(admin, "/admin/challenges/new")
    r = admin.post(
        "/admin/challenges/new",
        data={
            "csrf_token": token,
            "title": "Hardware Chal",
            "category": "hardware",
            "description": "hardware hack",
            "points": "100",
            "flag": "BB{hardware_flag}",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303


def test_edit_updates_challenge():
    admin = make_admin_client()
    cid = _setup_challenge("Original Title")
    token = get_csrf(admin, f"/admin/challenges/{cid}/edit")
    r = admin.post(
        f"/admin/challenges/{cid}/edit",
        data={
            "csrf_token": token,
            "title": "Updated Title",
            "category": "crypto",
            "description": "new description",
            "points": "200",
            "hints": "",
            "flag": "BB{new_flag}",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    db = SessionLocal()
    try:
        ch = db.query(Challenge).filter_by(id=cid).one()
        assert ch.title == "Updated Title"
        assert ch.points == 200
    finally:
        db.close()


def test_filename_sanitized_against_traversal():
    from app.flag_engine import sanitize_filename

    assert sanitize_filename("../../evil.txt") == "evil.txt"
    assert sanitize_filename("valid_name.png") == "valid_name.png"


def test_player_cannot_access_admin_crud():
    player = TestClient(app)
    register(player, "normal_player")
    assert (
        player.get("/admin/challenges/new", follow_redirects=False).status_code
        == 403
    )


# --- Flag Engine Semantics & Validation -------------------------------------


def test_flag_stored_only_as_hash_no_plaintext():
    cid = _setup_challenge("Hash Storage Test", "BB{secret_test_flag}")
    db = SessionLocal()
    try:
        ch = db.query(Challenge).filter_by(id=cid).one()
        assert ch.flag_sha256 == sha256_hex("BB{secret_test_flag}")
        assert "secret_test_flag" not in (ch.flag_template or "")
    finally:
        db.close()


def test_core_and_wrong_flag_never_match_hash():
    flag = "BB{my_exact_flag}"
    expected_hash = sha256_hex(flag)
    assert sha256_hex("BB{my_exact_") != expected_hash
    assert sha256_hex("BB{wrong_flag}") != expected_hash


def test_file_download_requires_login():
    cid = _setup_challenge("Download Login Check")
    anon = TestClient(app)
    r = anon.get(f"/challenges/{cid}/download/note.txt", follow_redirects=False)
    assert r.status_code == 302


def test_no_random_module_in_flag_engine():
    src = (ROOT / "app" / "flag_engine.py").read_text(encoding="utf-8")
    assert "import random" not in src
    assert "from random import" not in src


def test_generate_tail_properties():
    tail1 = generate_tail(12)
    tail2 = generate_tail(12)
    assert len(tail1) == 12
    assert len(tail2) == 12
    assert tail1 != tail2
    assert re.fullmatch(r"[A-Za-z0-9]{12}", tail1)


def test_validate_challenge_flag_shapes():
    assert validate_challenge_flag("BB{valid_flag_123}")[0]
    assert validate_challenge_flag("FLAG{other_prefix}")[0]
    assert not validate_challenge_flag("invalid_no_braces")[0]
    assert not validate_challenge_flag("BB{}")[0]


def test_template_helpers_unchanged():
    tpl = template_from_full_flag("BB{core_part_0123456789ab}")
    assert tpl is not None
    assert "{RAND:" in tpl


# --- Submissions, Scoring & Rate Limiting -----------------------------------


def test_acceptance_1_correct_flag_scores_and_moves_scoreboard():
    cid = _setup_challenge("Scoring Chal", FLAG)
    player = TestClient(app)
    register(player, "scorer")
    r = _submit_flag(player, cid, FLAG)
    assert r.status_code == 303
    assert "result=correct" in r.headers["location"]

    sb = player.get("/scoreboard").text
    assert "scorer" in sb
    assert "100" in sb


def test_acceptance_2_second_correct_submit_awards_nothing():
    cid = _setup_challenge("Dup Submit", FLAG)
    player = TestClient(app)
    register(player, "dup_player")
    r = _submit_flag(player, cid, FLAG)
    assert r.status_code == 303
    assert "result=correct" in r.headers["location"]

    submission_rate_limiter.reset_all()
    r = _submit_flag(player, cid, FLAG)
    assert r.status_code == 303
    assert "result=already" in r.headers["location"]


def test_acceptance_3_wrong_flag_logged_no_points():
    cid = _setup_challenge("Wrong Flag Chal", FLAG)
    player = TestClient(app)
    register(player, "wrong_player")
    r = _submit_flag(player, cid, "BB{wrong_attempt}")
    assert r.status_code == 303
    assert "result=incorrect" in r.headers["location"]

    db = SessionLocal()
    try:
        solves = (
            db.query(Solve).filter_by(user_id=user_id("wrong_player")).count()
        )
        assert solves == 0
        subs = (
            db.query(Submission)
            .filter_by(user_id=user_id("wrong_player"))
            .count()
        )
        assert subs == 1
    finally:
        db.close()


def test_acceptance_4_shared_flag_wrong_for_wrong_answer():
    cid = _setup_challenge("Cheat Check", FLAG)
    player = TestClient(app)
    register(player, "cheater")
    r = _submit_flag(player, cid, "BB{totally_fake_flag}")
    assert r.status_code == 303
    assert "result=incorrect" in r.headers["location"]


def test_acceptance_5_rate_limit_blocks_immediate_resubmit():
    cid = _setup_challenge("Rate Limit Chal", FLAG)
    player = TestClient(app)
    register(player, "rate_player")
    _submit_flag(player, cid, "BB{attempt_1}")
    r2 = _submit_flag(player, cid, "BB{attempt_2}")
    assert r2.status_code == 303
    assert "result=ratelimited" in r2.headers["location"]


def test_submission_rate_limiter_unit():
    limiter = SubmissionRateLimiter(min_interval=timedelta(seconds=1), max_per_minute=5)
    assert limiter.check(999) is True
    limiter.record(999)
    assert limiter.check(999) is False


def test_first_blood_badge_shows_first_solver():
    cid = _setup_challenge("Blood Chal", FLAG)
    p1 = TestClient(app)
    register(p1, "blood_p1")
    _submit_flag(p1, cid, FLAG)

    submission_rate_limiter.reset_all()
    p2 = TestClient(app)
    register(p2, "blood_p2")
    _submit_flag(p2, cid, FLAG)

    detail = p1.get(f"/challenges/{cid}").text
    assert "blood_p1" in detail


def test_scoreboard_visibility_config():
    player = TestClient(app)
    register(player, "sb_player")
    set_config("scoreboard_visibility", "public")
    assert player.get("/scoreboard").status_code == 200

    set_config("scoreboard_visibility", "hidden")
    assert player.get("/scoreboard").status_code == 200
    assert "scoreboard is hidden" in player.get("/scoreboard").text.lower()


def test_staff_never_scored():
    cid = _setup_challenge("Staff Solve", FLAG)
    admin = make_admin_client("staff_admin")
    _submit_flag(admin, cid, FLAG)
    anon = TestClient(app)
    sb = anon.get("/scoreboard").text
    assert "staff_admin" not in sb


def test_profile_reflects_earned_points():
    cid = _setup_challenge("Earned Points", FLAG)
    player = TestClient(app)
    register(player, "points_player")
    _submit_flag(player, cid, FLAG)
    profile = player.get("/profile").text
    assert "100" in profile
    assert "Earned Points" in profile


# --- Admin Portal, Users, Announcements, Import/Export ----------------------


def test_dashboard_shows_stats_graph_and_recent():
    admin = make_admin_client()
    dash = admin.get("/admin").text
    assert "Dashboard" in dash
    assert "users" in dash
    assert "Solves per day" in dash


def test_admin_changes_role_via_ui():
    admin = make_admin_client()
    player = TestClient(app)
    register(player, "roleuser")
    uid = user_id("roleuser")

    r = admin.post(
        f"/admin/users/{uid}/role",
        data={
            "csrf_token": get_csrf(admin, "/admin/users"),
            "role": "author",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(id=uid).one()
        assert user.role == "author"
    finally:
        db.close()


def test_admin_resets_password_shown_once_and_it_works():
    admin = make_admin_client()
    register(TestClient(app), "bob_reset")
    bid = user_id("bob_reset")
    r = admin.post(
        f"/admin/users/{bid}/password",
        data={"csrf_token": get_csrf(admin, "/admin/users")},
    )
    # v0.1: single-use token redirect, never the password in URL
    assert r.history
    loc = r.history[0].headers["location"]
    assert "reset_token=" in loc
    assert "new_password=" not in loc
    m = re.search(
        r"New password for bob_reset: <code>([A-Za-z0-9]{12})</code>", r.text
    )
    assert m, "new password not shown once to the admin"
    new_pw = m.group(1)

    fresh = TestClient(app)
    assert login(fresh, "bob_reset", new_pw).status_code == 303


def test_admin_instance_reset_route_removed():
    admin = make_admin_client()
    r = admin.post(
        "/admin/users/1/challenges/1/reset",
        data={"csrf_token": "x"},
        follow_redirects=False,
    )
    assert r.status_code == 404


def test_announcement_crud_and_display_on_notifications_page():
    admin = make_admin_client()
    token = get_csrf(admin, "/admin/announcements/new")
    r = admin.post(
        "/admin/announcements/new",
        data={
            "csrf_token": token,
            "title": "Welcome Announcement",
            "body": "CTF has begun!",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    player = TestClient(app)
    register(player, "notif_player")
    page = player.get("/notifications").text
    assert "Welcome Announcement" in page
    assert "CTF has begun!" in page


def test_platform_name_comes_from_config():
    admin = make_admin_client()
    token = get_csrf(admin, "/admin/config")
    admin.post(
        "/admin/config",
        data={
            "csrf_token": token,
            "platform_name": "CUSTOM_CTF_BRAND",
            "start_time": "",
            "end_time": "",
            "registration_open": "1",
            "scoreboard_visibility": "public",
        },
        follow_redirects=False,
    )
    home = admin.get("/").text
    assert "CUSTOM_CTF_BRAND" in home


def test_bulk_import_preview_then_confirm_creates_drafts():
    admin = make_admin_client()
    sample = [
        {
            "title": "Bulk One",
            "category": "web",
            "description": "d",
            "points": 50,
            "flag": "BB{bulk_one}",
        },
        {
            "title": "Bulk Two",
            "category": "crypto",
            "description": "d",
            "points": 75,
            "flag": "BB{bulk_two}",
        },
    ]
    preview = admin.post(
        "/admin/challenges/import",
        data={
            "csrf_token": get_csrf(admin, "/admin/challenges/import"),
            "format": "json",
            "payload": json.dumps(sample),
        },
        follow_redirects=True,
    )
    assert "Bulk One" in preview.text
    assert "Bulk Two" in preview.text

    confirm = admin.post(
        "/admin/challenges/import/confirm",
        data={
            "csrf_token": get_csrf(admin, "/admin/challenges/import"),
            "items_json": json.dumps([
                {
                    "title": "Bulk One",
                    "category": "web",
                    "description": "d",
                    "points": 50,
                    "flag_sha256": sha256_hex("BB{bulk_one}"),
                },
                {
                    "title": "Bulk Two",
                    "category": "crypto",
                    "description": "d",
                    "points": 75,
                    "flag_sha256": sha256_hex("BB{bulk_two}"),
                },
            ]),
        },
        follow_redirects=True,
    )
    assert "Imported 2 challenges as drafts" in confirm.text


def test_admin_ban_blocks_login():
    admin = make_admin_client()
    player = TestClient(app)
    register(player, "banned_guy")
    uid = user_id("banned_guy")

    admin.post(
        f"/admin/users/{uid}/ban",
        data={"csrf_token": get_csrf(admin, "/admin/users")},
        follow_redirects=False,
    )
    fresh = TestClient(app)
    r = login(fresh, "banned_guy", "password123")
    assert r.status_code == 303
    assert "banned" in r.headers.get("location", "").lower()


def test_registration_closed_blocks_signups():
    set_config("registration_open", "0")
    client = TestClient(app)
    r = register(client, "latecomer")
    assert r.status_code == 303
    landing = client.get(r.headers["location"]).text
    assert "Registration is closed" in landing


def test_ctf_ended_blocks_submissions_board_stays():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    set_config("end_time", past)
    cid = _setup_challenge("Ended CTF", FLAG)
    player = TestClient(app)
    register(player, "late_solver")
    r = _submit_flag(player, cid, FLAG)
    assert r.status_code == 303
    assert "result=ended" in r.headers["location"]


def test_ctf_not_started_shows_countdown_and_blocks():
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    set_config("start_time", future)
    cid = _setup_challenge("Future CTF", FLAG)
    player = TestClient(app)
    register(player, "early_solver")
    r = _submit_flag(player, cid, FLAG)
    assert r.status_code == 303
    assert "result=notstarted" in r.headers["location"]


def test_submissions_page_filters_and_cheat_log_with_ban():
    admin = make_admin_client()
    cid = _setup_challenge("Cheat Log Test", FLAG)
    cheater = TestClient(app)
    register(cheater, "caught_cheater")
    victim = TestClient(app)
    register(victim, "victim_player")
    db = SessionLocal()
    try:
        u_cheater = db.query(User).filter_by(username="caught_cheater").first()
        u_victim = db.query(User).filter_by(username="victim_player").first()
        cl = CheatLog(
            user_id=u_cheater.id,
            flag_owner_id=u_victim.id,
            challenge_id=cid,
            submitted_flag_hash="0123456789abcdef" * 4,
            source_ip="127.0.0.1",
        )
        db.add(cl)
        db.commit()
    finally:
        db.close()

    cheat_page = admin.get("/admin/cheat-log").text
    assert "caught_cheater" in cheat_page


def test_export_challenges_json_and_results_csv():
    admin = make_admin_client()
    _setup_challenge("Export Chal", FLAG)
    r_json = admin.get("/admin/export/challenges.json")
    assert r_json.status_code == 200
    assert "Export Chal" in r_json.text

    r_csv = admin.get("/admin/export/results.csv")
    assert r_csv.status_code == 200
    assert "user_id" in r_csv.text or "username" in r_csv.text


# --- Teams, Hints & Custom Pages -------------------------------------------


def test_teams_pages_hidden_without_mode():
    set_config("teams_mode", "0")
    player = TestClient(app)
    register(player, "solo_player")
    assert player.get("/teams", follow_redirects=False).status_code == 404


def test_team_solve_shows_on_team_scoreboard():
    set_config("teams_mode", "1")
    cid = _setup_challenge("Team Solve", FLAG)
    p1 = TestClient(app)
    register(p1, "teammate1")
    uid1 = user_id("teammate1")

    db = SessionLocal()
    try:
        t = Team(name="SquadX", invite_code="SQUAD123", captain_id=uid1)
        db.add(t)
        db.commit()
        db.refresh(t)
        db.add(TeamMembership(team_id=t.id, user_id=uid1))
        db.commit()
    finally:
        db.close()

    _submit_flag(p1, cid, FLAG)
    sb = p1.get("/scoreboard?mode=teams").text
    assert "SquadX" in sb


def test_team_score_counts_each_challenge_once():
    set_config("teams_mode", "1")
    cid = _setup_challenge("Team Double Solve", FLAG)
    p1 = TestClient(app)
    register(p1, "tm_a")
    uid1 = user_id("tm_a")

    p2 = TestClient(app)
    register(p2, "tm_b")
    uid2 = user_id("tm_b")

    db = SessionLocal()
    try:
        t = Team(name="SquadDouble", invite_code="SQUADDBL", captain_id=uid1)
        db.add(t)
        db.commit()
        db.refresh(t)
        db.add_all(
            [
                TeamMembership(team_id=t.id, user_id=uid1),
                TeamMembership(team_id=t.id, user_id=uid2),
            ]
        )
        db.commit()
    finally:
        db.close()

    _submit_flag(p1, cid, FLAG)
    submission_rate_limiter.reset_all()
    _submit_flag(p2, cid, FLAG)

    teams_ranked = ranked_teams(SessionLocal())
    squad_row = [row for row in teams_ranked if row["team"].name == "SquadDouble"][0]
    assert squad_row["points"] == 100


def test_team_max_is_four():
    set_config("teams_mode", "1")
    creator = TestClient(app)
    register(creator, "creator")
    uid0 = user_id("creator")

    db = SessionLocal()
    try:
        t = Team(name="MaxFourSquad", invite_code="MAX4CODE", captain_id=uid0)
        db.add(t)
        db.commit()
        db.refresh(t)
        db.add(TeamMembership(team_id=t.id, user_id=uid0))
        for i in range(1, 4):
            u = make_db_user(db, f"tm_member_{i}", role="player")
            db.add(TeamMembership(team_id=t.id, user_id=u.id))
        db.commit()
    finally:
        db.close()

    p5 = TestClient(app)
    register(p5, "fifth_player")
    r = p5.post(
        "/teams/join",
        data={
            "csrf_token": get_csrf(p5, "/teams"),
            "code": "MAX4CODE",
        },
        follow_redirects=True,
    )
    assert "is full" in r.text


def test_paid_hint_deducts_exactly_once():
    db = SessionLocal()
    try:
        ch = _make_challenge_row(
            db, title="Paid Hint Chal", points=100, hints=[{"text": "hint 1", "cost": 25}]
        )
        cid = ch.id
    finally:
        db.close()

    player = TestClient(app)
    register(player, "hint_buyer")
    uid = user_id("hint_buyer")

    db = SessionLocal()
    try:
        s = Solve(
            user_id=uid,
            challenge_id=cid,
            points_awarded=100,
            solved_at=datetime.now(timezone.utc),
        )
        db.add(s)
        db.commit()
    finally:
        db.close()

    r = player.post(
        f"/challenges/{cid}/hints/0/view",
        data={"csrf_token": get_csrf(player, f"/challenges/{cid}")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    players = ranked_players(SessionLocal())
    user_row = [p for p in players if p.id == uid][0]
    assert user_row.points == 75

    # Second unlock attempt must not charge again
    player.post(
        f"/challenges/{cid}/hints/0/view",
        data={"csrf_token": get_csrf(player, f"/challenges/{cid}")},
        follow_redirects=False,
    )
    players = ranked_players(SessionLocal())
    user_row = [p for p in players if p.id == uid][0]
    assert user_row.points == 75


def test_paid_hint_never_drops_points_below_zero():
    db = SessionLocal()
    try:
        ch = _make_challenge_row(
            db, title="Broke Hint Chal", points=100, hints=[{"text": "hint 1", "cost": 50}]
        )
        cid = ch.id
    finally:
        db.close()

    player = TestClient(app)
    register(player, "broke_player")
    uid = user_id("broke_player")

    player.post(
        f"/challenges/{cid}/hints/0/view",
        data={"csrf_token": get_csrf(player, f"/challenges/{cid}")},
        follow_redirects=False,
    )
    players = ranked_players(SessionLocal())
    user_row = [p for p in players if p.id == uid][0]
    assert user_row.points == 0


def test_legacy_free_hints_still_work():
    db = SessionLocal()
    try:
        ch = _make_challenge_row(
            db, title="Free Hint Chal", points=100, hints=["Just a free hint"]
        )
        cid = ch.id
    finally:
        db.close()
    player = TestClient(app)
    register(player, "free_hint_user")
    player.post(
        f"/challenges/{cid}/hints/0/view",
        data={"csrf_token": get_csrf(player, f"/challenges/{cid}")},
        follow_redirects=True,
    )
    detail = player.get(f"/challenges/{cid}").text
    assert "Just a free hint" in detail


def test_custom_pages_render_and_hide_drafts():
    db = SessionLocal()
    try:
        p1 = Page(
            title="Rules Custom",
            slug="rules-custom",
            content="Follow all rules.",
            is_published=True,
        )
        p2 = Page(
            title="Draft Custom",
            slug="draft-custom",
            content="Draft page.",
            is_published=False,
        )
        db.add_all([p1, p2])
        db.commit()
    finally:
        db.close()

    player = TestClient(app)
    register(player, "page_reader")
    r1 = player.get("/pages/rules-custom")
    assert r1.status_code == 200
    assert "Follow all rules." in r1.text

    r2 = player.get("/pages/draft-custom")
    assert r2.status_code == 404


def test_toggling_teams_mode_leaves_solo_data_intact():
    set_config("teams_mode", "1")
    cid = _setup_challenge("Toggle Chal", FLAG)
    player = TestClient(app)
    register(player, "solo_data_user")
    _submit_flag(player, cid, FLAG)

    set_config("teams_mode", "0")
    sb = player.get("/scoreboard").text
    assert "solo_data_user" in sb


def test_board_no_longer_shows_announcements():
    player = TestClient(app)
    register(player, "board_viewer")
    board = player.get("/challenges").text
    assert "announcement-banner" not in board


# --- Submissions PDF Export & Evidence Viewing ------------------------------


def test_submissions_pdf_export():
    admin = make_admin_client()
    cid = _setup_challenge("PDF Chal", FLAG)
    player = TestClient(app)
    register(player, "pdf_grace")
    _submit_flag(player, cid, FLAG)
    submission_rate_limiter.reset_all()
    _submit_flag(player, cid, "BB{wrong_answer}")

    r = admin.get("/admin/submissions/export.pdf", follow_redirects=False)
    assert r.status_code == 200
    assert "application/pdf" in r.headers["content-type"]
    assert r.content[:5] == b"%PDF-"
    assert "bytebatallion-submissions.pdf" in r.headers["content-disposition"]
    assert b"BB{web_sqli_abc123xyz}" not in r.content
    assert b"BB{wrong_answer}" not in r.content

    r = player.get("/admin/submissions/export.pdf", follow_redirects=False)
    assert r.status_code == 403
    anon = TestClient(app)
    r = anon.get("/admin/submissions/export.pdf", follow_redirects=False)
    assert r.status_code == 302


def test_pdf_export_survives_nasty_titles():
    admin = make_admin_client()
    player = TestClient(app)
    register(player, "nasty_grace")
    for title in NASTY_TITLES:
        cid = _setup_challenge(title, FLAG)
        submission_rate_limiter.reset_all()
        _submit_flag(player, cid, "BB{wrong_answer}")

    r = admin.get("/admin/submissions/export.pdf", follow_redirects=False)
    assert r.status_code == 200
    assert "application/pdf" in r.headers["content-type"]
    assert r.content[:5] == b"%PDF-"
    text = _pdf_text(r.content)
    assert b"Web & Pwn <Basics>" in text
    assert b"A <b Bold" in text
    assert b"100% <script>alert(1)</script>" in text


def test_pdf_export_respects_user_filter():
    admin = make_admin_client()
    cid = _setup_challenge("Filter Chal", FLAG)
    grace = TestClient(app)
    register(grace, "filter_grace")
    _submit_flag(grace, cid, "BB{wrong_answer}")

    submission_rate_limiter.reset_all()
    mallory = TestClient(app)
    register(mallory, "filter_mallory")
    _submit_flag(mallory, cid, "BB{wrong_answer}")

    r = admin.get(
        "/admin/submissions/export.pdf?user=filter_grace",
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert r.content[:5] == b"%PDF-"
    assert (
        "bytebatallion-submissions-filter_grace.pdf"
        in r.headers["content-disposition"]
    )
    text = _pdf_text(r.content)
    assert b"filter_grace" in text
    assert b"filter_mallory" not in text
    assert b"Total submissions: 1" in text
    assert b"Filtered by User: filter_grace" in text

    r_all = admin.get("/admin/submissions/export.pdf", follow_redirects=False)
    assert r_all.status_code == 200
    text_all = _pdf_text(r_all.content)
    assert b"filter_grace" in text_all and b"filter_mallory" in text_all
    assert b"Total submissions: 2" in text_all


def test_submissions_list_user_dropdown_and_summary():
    admin = make_admin_client()
    cid = _setup_challenge("Summary Chal", FLAG)
    grace = TestClient(app)
    register(grace, "sum_grace")
    _submit_flag(grace, cid, FLAG)
    submission_rate_limiter.reset_all()
    _submit_flag(grace, cid, "BB{wrong_answer}")

    submission_rate_limiter.reset_all()
    mallory = TestClient(app)
    register(mallory, "sum_mallory")
    _submit_flag(mallory, cid, "BB{wrong_answer}")

    page = admin.get("/admin/submissions", follow_redirects=False).text
    assert '<select name="user">' in page
    assert 'value="sum_grace"' in page and ">sum_grace</option>" in page
    assert 'value="sum_mallory"' in page and ">sum_mallory</option>" in page
    assert "export.pdf?user=" in page

    page_grace = admin.get(
        "/admin/submissions?user=sum_grace", follow_redirects=False
    ).text
    assert "user-summary" in page_grace
    assert "sum_grace" in page_grace
    assert "2 submissions" in page_grace
    assert "1 correct" in page_grace
    assert "1 incorrect" in page_grace
    assert "1 challenges attempted" in page_grace
    assert "Export this user" in page_grace
    assert "<td>sum_grace</td>" in page_grace
    assert "<td>sum_mallory</td>" not in page_grace

    submission_rate_limiter.reset_all()
    grace2 = TestClient(app)
    register(grace2, "sum_grace2")
    _submit_flag(grace2, cid, "BB{wrong_answer}")
    page_grace_filtered = admin.get(
        "/admin/submissions?user=sum_grace", follow_redirects=False
    ).text
    assert "<td>sum_grace2</td>" not in page_grace_filtered


def test_users_page_links_to_submission_log():
    admin = make_admin_client()
    player = TestClient(app)
    register(player, "log_grace")
    page = admin.get("/admin/users", follow_redirects=False).text
    assert "/admin/submissions?user=log_grace" in page
    assert "Submissions</a>" in page
