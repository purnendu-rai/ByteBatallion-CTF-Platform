"""v3.4 — IST (Asia/Kolkata) display times + no [NN] // page prefixes.

Acceptance:
- the five pages the user named (Challenges, Scoreboard, Profile,
  Account Settings, Notifications) show their plain headings — the
  [01]/[02]/[03]/[05]/[07] prefixes are gone; the user then asked
  for the last two as well, so Admin [04] and Teams [06] are gone too
- every time the site shows is IST: created/submitted/detected
  timestamps, the CTF countdown, the admin config form labels, and
  the admin PDF export stamp; no "UTC" remains in any template
- the admin config form takes CTF start/end in IST and round-trips
  the same wall time back
"""

import base64
import io
import re
import shutil
import zlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.flag_engine import DATA_DIR, sha256_hex
from app.main import app
from app.models import (
    Challenge, CheatLog, ConfigEntry, HintUnlock, Instance, Page,
    Solve, Submission, Team, TeamMembership, User, _parse_config_dt,
    get_config, set_config,
)
from app.security import (
    hash_password, login_rate_limiter, submission_rate_limiter,
)
from app.timeutil import IST, format_ist, to_ist

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
FLAG = "BB{ist_smoke_abc123}"


@pytest.fixture(autouse=True)
def _isolate():
    login_rate_limiter.reset_all()
    submission_rate_limiter.reset_all()
    _wipe_db()
    yield
    # Teardown wipe too: the countdown test leaves a FUTURE ctf_start_time
    # in ConfigEntry, and later suites (e.g. phase2's board tests) do not
    # wipe config themselves — without this, the board shows the countdown
    # instead of the challenge cards and they fail only in a full run.
    _wipe_db()
    for sub in ("challenges", "instances"):
        base = DATA_DIR / sub
        if base.is_dir():
            for p in base.iterdir():
                if p.is_dir():
                    shutil.rmtree(p)


def _wipe_db():
    db = SessionLocal()
    try:
        db.query(Solve).delete()
        db.query(Submission).delete()
        db.query(CheatLog).delete()
        db.query(HintUnlock).delete()
        db.query(Instance).delete()
        db.query(TeamMembership).delete()
        db.query(Team).delete()
        db.query(Page).delete()
        db.query(ConfigEntry).delete()
        db.query(Challenge).delete()
        db.query(User).delete()
        db.commit()
    finally:
        db.close()


def get_csrf(client: TestClient, path: str) -> str:
    resp = client.get(path, follow_redirects=True)
    match = CSRF_RE.search(resp.text)
    assert match, f"csrf token not found on {path}"
    return match.group(1)


def pdf_text(content: bytes) -> str:
    """Readable text from a reportlab PDF.

    Page streams are ASCII85-encoded then Flate-compressed
    (``/Filter [ /ASCII85Decode /FlateDecode ]``), so decode both
    layers; fall back to the raw bytes if a stream is uncompressed.
    """
    parts = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", content, re.S):
        raw = m.group(1).strip()
        try:
            data = base64.a85decode(raw, adobe=True)
        except Exception:
            data = raw
        try:
            parts.append(zlib.decompress(data).decode("latin-1", "replace"))
        except Exception:
            parts.append(data.decode("latin-1", "replace"))
    return "\n".join(parts)


def login(client: TestClient, username: str, password: str = "password123"):
    r = client.post(
        "/auth/login",
        data={"csrf_token": get_csrf(client, "/auth/login"),
              "username": username, "password": password},
        follow_redirects=False,
    )
    assert r.status_code == 303
    return r


def make_user(username: str, role: str = "player"):
    db = SessionLocal()
    try:
        db.add(User(username=username, email=f"{username}@upsifs.in",
                    password_hash=hash_password("password123"), role=role))
        db.commit()
    finally:
        db.close()


def make_admin_client() -> TestClient:
    make_user("admin1", role="admin")
    client = TestClient(app)
    with client:
        login(client, "admin1")
        return client


def make_player_client(username: str = "alice") -> TestClient:
    make_user(username)
    client = TestClient(app)
    with client:
        login(client, username)
        return client


def seed_challenge(admin: TestClient) -> int:
    token = get_csrf(admin, "/admin/challenges/new")
    r = admin.post(
        "/admin/challenges/new",
        data={"csrf_token": token, "title": "IST Challenge",
              "category": "misc", "description": "ist test",
              "points": "10", "hints": "", "flag": FLAG},
        files=[("files", ("note.txt", io.BytesIO(b"hello"), "text/plain"))],
        follow_redirects=False)
    assert r.status_code == 303
    cid = int(re.search(r"/admin/challenges/(\d+)/edit",
                        r.headers["location"]).group(1))
    admin.post(f"/admin/challenges/{cid}/publish",
               data={"csrf_token": get_csrf(admin, f"/admin/challenges/{cid}/edit")},
               follow_redirects=False)
    return cid


# --- unit: the conversion itself ------------------------------------------------


def test_to_ist_converts_utc_and_naive():
    # 12:00 UTC == 17:30 IST, winter and summer alike (IST has no DST).
    utc_noon = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    assert to_ist(utc_noon).strftime("%H:%M") == "17:30"
    assert to_ist(utc_noon).tzinfo == IST
    # A naive datetime is read as UTC, then shifted.
    assert to_ist(datetime(2026, 1, 15, 12, 0)).strftime("%H:%M") == "17:30"
    assert to_ist(None) is None
    assert format_ist(None) == ""
    assert format_ist(utc_noon) == "15 Jan 2026 17:30"
    assert format_ist(utc_noon, "%H:%M") == "17:30"


def test_parse_config_dt_treats_naive_as_ist():
    dt = _parse_config_dt("2026-11-15T10:00")
    assert dt is not None
    assert dt.tzinfo == IST
    assert (dt.hour, dt.minute) == (10, 0)
    # An explicit offset is respected, not overwritten.
    dt = _parse_config_dt("2026-11-15T10:00:00+00:00")
    assert dt.tzinfo == timezone.utc
    assert _parse_config_dt("") is None
    assert _parse_config_dt("not-a-date") is None


# --- the five headings: prefixes gone -------------------------------------------


def test_five_pages_show_plain_headings():
    player = make_player_client()
    checks = [
        ("/challenges", "Challenges", "[01] //"),
        ("/scoreboard", "Scoreboard", "[02] //"),
        ("/profile", "Profile", "[03] //"),
        ("/settings", "Account Settings", "[05] //"),
        ("/notifications", "Notifications", "[07] //"),
    ]
    for path, heading, prefix in checks:
        html = player.get(path).text
        assert prefix not in html, f"{path} still has its {prefix} prefix"
        assert heading in html, f"{path} lost its heading"
    # The last two prefixes went too (user's follow-up): Admin [04]
    # and Teams [06] are plain headings now as well.
    admin = make_admin_client()
    assert "[04] //" not in admin.get("/admin").text
    assert ">Admin<" in admin.get("/admin").text
    db = SessionLocal()
    try:
        set_config(db, "teams_mode", "true")
    finally:
        db.close()
    teams = player.get("/teams").text
    assert "[06] //" not in teams
    assert ">Teams<" in teams


# --- every displayed time is IST -------------------------------------------------


def test_settings_and_notifications_show_ist():
    player = make_player_client()
    db = SessionLocal()
    try:
        alice = db.query(User).filter_by(username="alice").one()
        expected = format_ist(alice.created_at, "%d %b %Y, %H:%M")
    finally:
        db.close()
    settings = player.get("/settings").text
    assert " IST" in settings
    assert expected + " IST" in settings
    assert "UTC" not in settings
    # An announcement's timestamp renders in IST too.
    admin = make_admin_client()
    admin.post("/admin/announcements/new",
               data={"csrf_token": get_csrf(admin, "/admin/announcements"),
                     "title": "Hello", "body": "IST check"},
               follow_redirects=False)
    notes = player.get("/notifications").text
    assert " IST" in notes
    assert "UTC" not in notes


def test_submission_times_show_ist_in_admin():
    admin = make_admin_client()
    cid = seed_challenge(admin)
    player = make_player_client()
    r = player.post(f"/submit/{cid}", data={
        "csrf_token": get_csrf(player, f"/challenges/{cid}"), "flag": FLAG},
        follow_redirects=True)
    assert "Correct" in r.text
    db = SessionLocal()
    try:
        sub = db.query(Submission).filter_by(challenge_id=cid).one()
        expected = format_ist(sub.submitted_at, "%d %b %H:%M:%S")
    finally:
        db.close()
    html = admin.get("/admin/submissions").text
    assert expected in html
    assert "Time (IST)" in html
    assert "UTC" not in html
    dashboard = admin.get("/admin").text
    assert "Time (IST)" in dashboard
    # The profile solves log shows the solve time in IST too.
    db = SessionLocal()
    try:
        solve = db.query(Solve).filter_by(challenge_id=cid).one()
        expected_solve = format_ist(solve.solved_at, "%B %d, %Y at %I:%M %p")
    finally:
        db.close()
    assert expected_solve in player.get("/profile").text
    # The admin PDF export stamps IST as well.
    pdf = admin.get("/admin/submissions/export.pdf")
    assert pdf.status_code == 200
    text = pdf_text(pdf.content)
    assert "IST" in text
    assert "UTC" not in text


def test_ctf_countdown_shows_ist_and_form_round_trips():
    admin = make_admin_client()
    start_ist = datetime.now(IST) + timedelta(hours=2)
    wall = start_ist.strftime("%Y-%m-%dT%H:%M")
    r = admin.post("/admin/config", data={
        "csrf_token": get_csrf(admin, "/admin/config"),
        "platform_name": "BYTE_BATALLION_CTF_PLATFORM",
        "ctf_start_time": wall, "ctf_end_time": "",
        "registration_open": "on", "scoreboard_visible": "on"},
        follow_redirects=False)
    assert r.status_code == 303
    # The form shows the same IST wall time back.
    config_html = admin.get("/admin/config").text
    assert f'value="{wall}"' in config_html
    assert "CTF start (IST)" in config_html
    assert "CTF end (IST)" in config_html
    assert "UTC" not in config_html
    # The public countdown reads in IST, with the JS hook intact.
    db = SessionLocal()
    try:
        cfg_start = _parse_config_dt(get_config(db, "ctf_start_time"))
    finally:
        db.close()
    expected = format_ist(cfg_start, "%d %b %Y %H:%M")
    with TestClient(app) as anon:
        home = anon.get("/").text
        assert "data-countdown-to" in home
        assert expected + " IST" in home
        assert "UTC" not in home
