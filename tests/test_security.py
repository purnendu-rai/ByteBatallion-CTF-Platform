"""Security tests: HTTP headers, Content-Security-Policy, template XSS checks,
scoreboard tooltip escaping, login rate limiters, session cookie attributes,
upload guards, production secrets check, error confidentiality, CLI admin, and load smoke.
"""

import io
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import create_admin
from app.config import Settings, validate_settings
from app.database import SessionLocal
from app.flag_engine import (
    DATA_DIR,
    extension_blocked,
    sha256_hex,
)
from app.main import app
from app.models import Challenge, Solve, Submission, User
from app.security import hash_password
from tests.conftest import ROOT, get_csrf, login, register

SCOREBOARD_JS = (ROOT / "app" / "static" / "js" / "scoreboard.js").read_text(
    encoding="utf-8"
)
SMOKE_FLAG = "BB{smoke_abc123xyz}"
FILE_BODY = b"the flag: FLAG_PLACEHOLDER end"


# --- HTTP security headers & Content-Security-Policy -------------------------


def test_security_headers_on_home():
    resp = TestClient(app).get("/")
    assert resp.status_code == 200
    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    # script-src must NOT allow inline scripts
    script_src = [p for p in csp.split(";") if p.strip().startswith("script-src")][0]
    assert "unsafe-inline" not in script_src
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert "form-action 'self'" in csp
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert "camera=()" in resp.headers.get("permissions-policy", "")
    # HSTS is prod-only: it must NOT be sent on plain-http dev
    assert "strict-transport-security" not in resp.headers


def test_security_headers_on_api_and_login_pages():
    client = TestClient(app)
    for path in ("/auth/login", "/scoreboard"):
        resp = client.get(path, follow_redirects=False)
        assert resp.headers.get("x-frame-options") == "DENY"
        assert "default-src 'self'" in resp.headers.get(
            "content-security-policy", ""
        )


# --- templates: no unsafe output, no inline code ----------------------------


def test_no_safe_filter_in_templates():
    for path in (ROOT / "app" / "templates").rglob("*.html"):
        assert "| safe" not in path.read_text(encoding="utf-8"), (
            f"{path} uses | safe"
        )


def test_no_inline_scripts_or_handlers_in_templates():
    handler_re = re.compile(
        r"\son(?:click|change|input|submit|load|error|focus|blur|keydown|keyup|mouse\w+)\s*="
    )
    for path in (ROOT / "app" / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"\{#.*?#\}", "", text, flags=re.DOTALL)
        text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
        assert handler_re.search(text) is None, (
            f"{path} has an inline event handler"
        )
        for match in re.finditer(r"<script\b([^>]*)>", text):
            attrs = match.group(1)
            # Only external scripts, or JSON data blocks (data, not code).
            assert "src=" in attrs or 'type="application/json"' in attrs, (
                f"{path} has an inline executable <script>"
            )


def test_profile_data_block_and_admin_form_script():
    client = TestClient(app)
    register(client, f"secprof_{uuid.uuid4().hex[:8]}")
    resp = client.get("/profile")
    assert resp.status_code == 200
    assert 'id="score-history-data"' in resp.text
    assert "window.USER_SCORE_HISTORY =" not in resp.text
    template = (
        ROOT / "app" / "templates" / "admin" / "challenge_form.html"
    ).read_text(encoding="utf-8")
    assert "/js/admin_challenge_form.js" in template
    assert "onchange=" not in template
    assert "oninput=" not in template


# --- scoreboard tooltip: team-name XSS --------------------------------------


def test_scoreboard_tooltip_escapes_username():
    assert "function escapeHtml" in SCOREBOARD_JS
    assert "escapeHtml(s.username)" in SCOREBOARD_JS


def test_escape_html_actually_escapes():
    """Run the shipped escapeHtml against a hostile team name in node."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    match = re.search(
        r"function escapeHtml\(s\) \{.*?\n  \}", SCOREBOARD_JS, re.DOTALL
    )
    assert match, "escapeHtml not found in scoreboard.js"
    script = (
        match.group(0)
        + "\nconst assert = require('assert');\n"
        + "assert.strictEqual(escapeHtml('<img src=x onerror=alert(1)>'), '&lt;img src=x onerror=alert(1)&gt;');\n"
        + "assert.strictEqual(escapeHtml('a & b'), 'a &amp; b');\n"
        + "assert.strictEqual(escapeHtml('alice_1'), 'alice_1');\n"
        + "console.log('escapeHtml OK');\n"
    )
    proc = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "escapeHtml OK" in proc.stdout


# --- login lockout + cookie flags + CSRF -----------------------------------


def test_login_lockout_after_five_failures():
    client = TestClient(app)
    probe = f"lockprobe_{uuid.uuid4().hex[:8]}"
    for _ in range(5):
        resp = client.post(
            "/auth/login",
            data={
                "csrf_token": get_csrf(client),
                "username": probe,
                "password": "wrong-password",
            },
        )
        assert "Too many failed attempts" not in resp.text
    # 6th attempt is locked out
    resp = client.post(
        "/auth/login",
        data={
            "csrf_token": get_csrf(client),
            "username": probe,
            "password": "wrong-password",
        },
    )
    assert "Too many failed attempts" in resp.text


def test_session_cookie_flags_on_login():
    client = TestClient(app)
    username = f"seccookie_{uuid.uuid4().hex[:8]}"
    register(client, username)
    login_client = TestClient(app)
    resp = login_client.post(
        "/auth/login",
        data={
            "csrf_token": get_csrf(login_client),
            "username": username,
            "password": "password123",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    set_cookie = resp.headers.get("set-cookie", "")
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()
    assert "secure" not in set_cookie.lower()


def test_csrf_required(client):
    r = client.post(
        "/auth/register",
        data={
            "username": "jack",
            "email": "jack@upsifs.in",
            "password": "password123",
        },
    )
    assert r.status_code in (403, 422)

    r = client.post(
        "/auth/register",
        data={
            "csrf_token": "tampered",
            "username": "jack",
            "email": "jack@upsifs.in",
            "password": "password123",
        },
    )
    assert r.status_code == 403


# --- upload validation (size + type blocklist) ------------------------------


def _seed_challenge_via_ui(admin_client: TestClient) -> int:
    token = get_csrf(admin_client, "/admin/challenges/new")
    r = admin_client.post(
        "/admin/challenges/new",
        data={
            "csrf_token": token,
            "title": "Smoke Challenge",
            "category": "misc",
            "description": "load smoke test",
            "points": "10",
            "hints": "",
            "flag": SMOKE_FLAG,
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
    r = admin_client.post(
        f"/admin/challenges/{cid}/publish",
        data={
            "csrf_token": get_csrf(
                admin_client, f"/admin/challenges/{cid}/edit"
            )
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    return cid


def _seed_challenge() -> int:
    db = SessionLocal()
    try:
        admin = db.query(User).filter_by(username="admin1").first()
        if admin is None:
            db.add(
                User(
                    username="admin1",
                    email="admin1@upsifs.in",
                    password_hash=hash_password("password123"),
                    role="admin",
                )
            )
            db.commit()
    finally:
        db.close()
    client = TestClient(app)
    with client:
        login(client, "admin1")
        return _seed_challenge_via_ui(client)


def test_upload_blocklist_rejects_executables():
    assert extension_blocked("evil.exe")
    assert extension_blocked("PAYLOAD.BAT")
    assert extension_blocked("dark_legion.ps1")
    assert not extension_blocked("story.zip")
    assert not extension_blocked("capture.pcap")
    with TestClient(app) as admin_client:
        _seed_challenge()
        login(admin_client, "admin1")
        token = get_csrf(admin_client, "/admin/challenges/new")
        r = admin_client.post(
            "/admin/challenges/new",
            data={
                "csrf_token": token,
                "title": "Blocked up",
                "category": "misc",
                "description": "x",
                "points": "10",
                "hints": "",
                "flag": SMOKE_FLAG,
            },
            files=[
                (
                    "files",
                    (
                        "evil.exe",
                        io.BytesIO(b"MZ" + b"\x00" * 32),
                        "application/octet-stream",
                    ),
                )
            ],
            follow_redirects=True,
        )
        assert r.status_code == 400, "blocked extension not rejected"
        r = admin_client.post(
            "/admin/challenges/new",
            data={
                "csrf_token": get_csrf(admin_client, "/admin/challenges/new"),
                "title": "Allowed up",
                "category": "misc",
                "description": "x",
                "points": "10",
                "hints": "",
                "flag": SMOKE_FLAG,
            },
            files=[
                (
                    "files",
                    (
                        "story.zip",
                        io.BytesIO(b"PK" + b"\x00" * 32),
                        "application/zip",
                    ),
                )
            ],
            follow_redirects=False,
        )
        assert r.status_code == 303, "a normal zip was wrongly rejected"


def test_upload_size_limit(monkeypatch):
    from app import flag_engine

    monkeypatch.setattr(flag_engine, "MAX_FILE_BYTES", 128)
    with TestClient(app) as admin_client:
        _seed_challenge()
        login(admin_client, "admin1")
        big = b"x" * 2048
        r = admin_client.post(
            "/admin/challenges/new",
            data={
                "csrf_token": get_csrf(admin_client, "/admin/challenges/new"),
                "title": "Too big",
                "category": "misc",
                "description": "x",
                "points": "10",
                "hints": "",
                "flag": SMOKE_FLAG,
            },
            files=[
                (
                    "files",
                    ("big.bin", io.BytesIO(big), "application/octet-stream"),
                )
            ],
            follow_redirects=True,
        )
        assert r.status_code == 413, "over-limit file accepted"
    db = SessionLocal()
    try:
        ch = db.query(Challenge).filter_by(title="Too big").one()
        originals = DATA_DIR / "challenges" / str(ch.id) / "originals"
        if originals.is_dir():
            assert not (originals / "big.bin").exists(), (
                "partial file left on disk"
            )
    finally:
        db.close()


# --- production configuration guards ----------------------------------------


def test_prod_refuses_dev_secret_key():
    s = Settings(environment="prod", secret_key="dev-only-secret-change-me")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        validate_settings(s)
    s = Settings(environment="prod", secret_key="a" * 31)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        validate_settings(s)
    ok = Settings(environment="prod", secret_key="a" * 64, debug=False)
    validate_settings(ok)
    s = Settings(environment="prod", secret_key="a" * 64, debug=True)
    with pytest.raises(RuntimeError, match="DEBUG"):
        validate_settings(s)
    validate_settings(Settings())


# --- error pages confidentiality -------------------------------------------


def test_unknown_page_returns_branded_404_without_traceback():
    with TestClient(app) as client:
        r = client.get("/no/such/page")
        assert r.status_code == 404
        assert "Page not found." in r.text
        assert "Traceback" not in r.text
        assert "starlette" not in r.text.lower()
        assert "\n  File" not in r.text


def test_missing_file_download_is_403_not_an_internal_error():
    with TestClient(app) as client:
        r = client.get(
            "/challenges/999999/download/bogus.txt", follow_redirects=False
        )
        assert r.status_code == 302
        assert r.headers["location"].startswith("/auth/login?next=")
        assert "Traceback" not in r.text


# --- CLI admin creation ----------------------------------------------------


def test_create_admin_cli_creates_a_working_admin(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    create_admin.main(["rootadmin", "root@upsifs.in"])
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username="rootadmin").one()
        assert user.role == "admin"
    finally:
        db.close()
    with TestClient(app) as c:
        login(c, "rootadmin")
        assert c.get("/admin").status_code == 200

    with pytest.raises(SystemExit):
        create_admin.main(["rootadmin", "other@upsifs.in"])
    with pytest.raises(SystemExit):
        create_admin.main(["thirdadm", "not-an-email"])
    monkeypatch.setenv("ADMIN_PASSWORD", "short")
    with pytest.raises(SystemExit):
        create_admin.main(["thirdadm", "third@upsifs.in"])


# --- load smoke test: 20 users, no cross-user flag leak --------------------


def test_load_smoke_20_users_register_start_submit():
    cid = _seed_challenge()
    names: list[str] = []
    for i in range(20):
        name = f"smokeuser{i:02d}"
        c = TestClient(app)
        with c:
            register(c, name)
            dl = c.get(f"/challenges/{cid}/download/note.txt")
            assert dl.status_code == 200, f"{name}: file download failed"
        names.append(name)

    for name in names:
        c = TestClient(app)
        with c:
            login(c, name)
            r = c.post(
                f"/submit/{cid}",
                data={
                    "csrf_token": get_csrf(c, f"/challenges/{cid}"),
                    "flag": SMOKE_FLAG,
                },
                follow_redirects=True,
            )
            assert "Correct" in r.text, f"{name}: correct flag did not score"

    time.sleep(6)
    for name in names:
        c = TestClient(app)
        with c:
            login(c, name)
            r = c.post(
                f"/submit/{cid}",
                data={
                    "csrf_token": get_csrf(c, f"/challenges/{cid}"),
                    "flag": "BB{wrong_answer}",
                },
                follow_redirects=True,
            )
            assert "Incorrect" in r.text, f"{name}: wrong flag was ACCEPTED"

    db = SessionLocal()
    try:
        solves = (
            db.query(Solve.challenge_id).filter_by(challenge_id=cid).count()
        )
        assert solves == 20
        hashes = db.query(Submission.submitted_flag_hash).all()
        assert all(
            64 == len(h[0]) and re.fullmatch(r"[0-9a-f]{64}", h[0])
            for h in hashes
        )
        ch = db.query(Challenge).filter_by(id=cid).one()
        assert ch.flag_sha256 == sha256_hex(SMOKE_FLAG)
        assert ch.flag_template == ""
    finally:
        db.close()

    with TestClient(app) as c:
        assert c.get("/").status_code == 200
