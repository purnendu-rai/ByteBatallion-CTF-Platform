"""UI, terminal emulator, animations, operator flip cards, and client-side styling tests.
"""

import base64
import re
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app.flag_engine import sha256_hex
from app.main import app
from app.models import Announcement, Challenge, User
from tests.conftest import (
    ROOT,
    SessionLocal,
    get_csrf,
    login,
    make_admin_client,
    make_db_user,
    register,
)

CSS = (ROOT / "app" / "static" / "css" / "style.css").read_text(
    encoding="utf-8"
)
HOME_JS = (ROOT / "app" / "static" / "js" / "home.js").read_text(
    encoding="utf-8"
)
TERM_JS = (ROOT / "app" / "static" / "js" / "terminal.js").read_text(
    encoding="utf-8"
)
STORY_JS = (ROOT / "app" / "static" / "js" / "story.js").read_text(
    encoding="utf-8"
)
CURSOR_JS = (ROOT / "app" / "static" / "js" / "cursor.js").read_text(
    encoding="utf-8"
)
NOTIF_JS = (ROOT / "app" / "static" / "js" / "notifications.js").read_text(
    encoding="utf-8"
)
BASE_HTML = (ROOT / "app" / "templates" / "base.html").read_text(
    encoding="utf-8"
)

HANDLES = (
    "HACKERP",
    "C1PH3R",
    "GH0ST",
    "V01D",
    "R4ZOR",
    "S1REN",
    "N3TRUNN3R",
    "B1NARY",
    "V3N0M",
    "SH4D0W",
    "F0RG3",
    "Z3R0D4Y",
)
IMAGES = tuple("/static/img/characters/%s.png" % h.lower() for h in HANDLES)
SIGS = (
    "First Bloods: 27",
    "Ciphers broken: 41",
    "Cases reconstructed: 33",
    "Shells popped: 58",
    "Payloads shipped: 64",
    "Doors opened: 89",
    "Networks owned: 52",
    "Binaries reversed: 73",
    "Databases drained: 47",
    "Messages hidden: 128",
    "Devices cracked: 36",
    "Zero-days found: 19",
)

FLAG_RE = re.compile(r"BB\{[A-Za-z0-9_]+\}")
FLAG = "BB{test_flag_value}"


def _caesar(text: str, shift: int) -> str:
    out = []
    for ch in text:
        if "a" <= ch <= "z":
            out.append(chr((ord(ch) - ord("a") + shift) % 26 + ord("a")))
        elif "A" <= ch <= "Z":
            out.append(chr((ord(ch) - ord("A") + shift) % 26 + ord("A")))
        else:
            out.append(ch)
    return "".join(out)


def _make_ui_challenge(
    title: str, category: str = "web", flag: str = FLAG
) -> int:
    db = SessionLocal()
    try:
        admin = db.query(User).filter_by(role="admin").first()
        if not admin:
            admin = make_db_user("admin_ui", role="admin")
        ch = Challenge(
            title=title,
            category=category,
            description="desc",
            points=100,
            author_id=admin.id,
            flag_template="",
            flag_sha256=sha256_hex(flag),
            hints=[],
            is_published=True,
        )
        db.add(ch)
        db.commit()
        return ch.id
    finally:
        db.close()


def _solve_ui_challenge(client: TestClient, cid: int, flag: str = FLAG):
    return client.post(
        f"/submit/{cid}",
        data={
            "csrf_token": get_csrf(client, f"/challenges/{cid}"),
            "flag": flag,
        },
        follow_redirects=False,
    )


# --- The Batallion (12 Operator Dossiers & Flip Cards) ----------------------


def test_home_has_battalion_section_and_story():
    home = TestClient(app).get("/").text
    assert 'id="battalion"' in home
    assert "[02] //" not in home
    assert (
        '<h2 class="battalion-title battalion-reveal"><span'
        ' class="battalion-name">THE BATALLION</span></h2>'
        in home
    )
    assert 'class="battalion-name">THE BATALLION</span>' in home
    assert "Twelve operators. One standing operation." in home
    assert "Four operators. One standing operation." not in home
    assert "that is how the Batallion was born" in home
    assert "no two soldiers carry the same orders" in home
    assert "It watches the board" in home
    assert "battalion-story" in home
    assert 'class="smoke-layer"' in home
    assert home.count('class="smoke smoke-') == 4


def test_home_has_all_twelve_flip_cards():
    home = TestClient(app).get("/").text
    for handle in HANDLES:
        assert handle in home
    for image in IMAGES:
        assert image in home
    assert home.count('class="flip-card battalion-reveal"') == 12
    assert "flip-card-inner" in home
    assert "flip-face flip-front" in home
    assert "flip-face flip-back" in home
    assert "[ HOVER / TAP TO REVEAL ]" in home
    assert "[+] FLIP" not in home
    assert "[x] BACK" in home
    for sig in SIGS:
        assert sig in home
    assert "Threat Level" in home
    assert "Critical" in home
    assert 'aria-pressed="false"' in home
    assert "Activate to flip the card" in home
    assert "/static/js/story.js" in home
    assert home.index("/static/js/home.js") < home.index("/static/js/story.js")
    assert home.index('id="terminal"') < home.index('id="battalion"')


def test_story_js_reveal_and_flip_rules():
    assert "IntersectionObserver" in STORY_JS
    assert "prefers-reduced-motion" in STORY_JS
    assert "aria-pressed" in STORY_JS
    assert "flipped" in STORY_JS
    assert "battalion-reveal" in STORY_JS
    assert "120" in STORY_JS
    assert "hover: none" in STORY_JS
    assert "touchOnly" in STORY_JS


def test_css_flip_rules_and_auth_card_centering():
    assert "transform-style: preserve-3d" in CSS
    assert "backface-visibility: hidden" in CSS
    assert "transition: transform 0.6s ease" in CSS
    assert ".flip-card-inner.flipped" in CSS
    assert "rotateY(180deg)" in CSS
    assert ".flip-card:hover .flip-card-inner" in CSS
    assert ".flip-card:focus-within .flip-card-inner" in CSS
    assert "repeat(3, minmax(0, 320px))" in CSS
    assert "repeat(2, minmax(0, 320px))" in CSS
    assert "repeat(auto-fit, minmax(240px, 1fr))" not in CSS
    assert "justify-content: center" in CSS
    reduced = CSS.split("@media (prefers-reduced-motion: reduce)")[1]
    assert ".flip-card-inner" in reduced
    assert ".battalion" in reduced
    auth = CSS.split(".auth-card {")[1].split("}")[0]
    assert "margin: 0 auto" in auth


def test_battalion_centred_glow_smoke_and_card_curve():
    battalion = CSS.split(".battalion {")[1].split("}")[0]
    assert "text-align: center" in battalion
    assert ".battalion-name" in CSS
    assert "text-shadow" in CSS
    assert "@keyframes battalion-glow" in CSS
    assert "animation: battalion-glow" in CSS
    story = CSS.split(".battalion-story {")[1].split("}")[0]
    assert "margin: 0 auto" in story
    assert "border-radius: 18px" in CSS
    assert "border-radius: 6px" not in CSS
    assert "border-radius: 0 !important" not in CSS
    assert "@keyframes card-glow" in CSS
    assert "animation: card-glow" in CSS
    assert ".flip-card:nth-child(3n+2)" in CSS
    assert "@keyframes card-pop" in CSS
    assert "animation: card-pop" in CSS
    assert "scale: 1.02" in CSS
    smoke_layer = CSS.split(".smoke-layer {")[1].split("}")[0]
    assert "position: fixed" in smoke_layer
    assert "z-index: 0" in smoke_layer
    assert "z-index: -1" not in smoke_layer
    assert "pointer-events: none" in smoke_layer
    assert "@keyframes smoke-drift" in CSS
    assert "animation: smoke-drift" in CSS
    assert "filter: blur(70px)" in CSS
    assert "rgba(0, 255, 65, 0.85)" in CSS
    assert "opacity: 0.28" in CSS
    assert ".smoke-d" in CSS
    main = CSS.split(".cyber-main, main {")[1].split("}")[0]
    assert "z-index: 1" in main
    footer = CSS.split(".cyber-footer {")[1].split("}")[0]
    assert "z-index: 1" in footer
    assert "z-index: 100" in CSS
    reduced = CSS.split("@media (prefers-reduced-motion: reduce)")[1]
    assert ".battalion-name" in reduced
    assert ".flip-face" in reduced
    assert ".flip-card" in reduced
    assert ".smoke" in reduced
    assert "animation: none" in reduced


def test_favicon_and_social_links():
    assert 'rel="icon"' in BASE_HTML
    assert "url_for('static', path='/img/logo.png')" in BASE_HTML
    home = TestClient(app).get("/").text
    assert 'rel="icon"' in home
    assert "/static/img/logo.png" in home
    assert 'class="battalion-social battalion-reveal"' in home
    assert home.count('class="social-link"') == 2
    assert 'href="https://www.instagram.com/byte__battalion"' in home
    assert 'href="https://www.linkedin.com/company/byte-battalion/"' in home
    assert "ByteBatallion on Instagram" in home
    assert "ByteBatallion on LinkedIn" in home
    assert "FOLLOW THE BATALLION" in home
    assert home.index('id="battalion"') < home.index("battalion-social")
    assert "social-link" in home and "<svg" in home


def test_custom_hacker_cursor():
    assert "cursor.js" in BASE_HTML
    assert "url_for('static', path='/js/cursor.js')" in BASE_HTML
    login_page = TestClient(app).get("/auth/login")
    assert login_page.status_code == 200
    assert "/static/js/cursor.js" in login_page.text
    home = TestClient(app).get("/").text
    assert "/static/js/cursor.js" in home
    assert "<script>" not in BASE_HTML
    assert "(hover: hover) and (pointer: fine)" in CURSOR_JS
    assert "requestAnimationFrame" in CURSOR_JS
    assert "prefers-reduced-motion" in CURSOR_JS
    assert "custom-cursor" in CURSOR_JS
    assert "#bb-cursor" in CSS
    assert "#bb-cursor-ghost" in CSS
    assert "pointer-events: none" in CSS
    assert "cursor: none !important" in CSS
    assert "html.custom-cursor" in CSS
    assert "cursor-hover" in CSS
    assert "cursor-text" in CSS
    assert "@media (hover: none), (pointer: coarse)" in CSS


def test_footer_says_hackerp_not_hackerzzz():
    home = TestClient(app).get("/").text
    assert "Made by HackerP" in home
    assert "Made by Hackerzzz" not in home


def test_login_page_has_centred_auth_card():
    login_page = TestClient(app).get("/auth/login")
    assert login_page.status_code == 200
    assert 'class="card auth-card"' in login_page.text


# --- Home Screen, Boot Overlay & Terminal Shell -----------------------------


def test_home_has_boot_overlay_and_home_js():
    home = TestClient(app).get("/").text
    assert 'id="boot-overlay"' in home
    assert 'role="status"' in home
    assert 'aria-busy="true"' in home
    assert (
        'id="boot-overlay"' in home
        and "hidden" in home.split('id="boot-overlay"')[1][:200]
    )
    assert "/static/js/home.js" in home
    assert 'id="boot-lines"' in home
    assert 'id="boot-progress"' in home


def test_home_hero_types_short_name_only_and_no_shortcuts_line():
    home = TestClient(app).get("/").text
    assert 'id="hero-title"' in home
    assert 'aria-label="BYTE_BATALLION"' in home
    h1 = home.split('id="hero-title"')[1].split("</h1>")[0]
    assert "BYTE_BATALLION" in h1
    assert "_CTF_PLATFORM" not in h1
    template = (ROOT / "app" / "templates" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "replace('_CTF_PLATFORM'" in template
    assert "no shortcuts" not in home
    assert "Be smart enough to get the flag" in home
    assert "./bytebatallion --capture-the-flag" in home


def test_home_terminal_markup_and_no_coming_soon():
    home = TestClient(app).get("/").text
    assert 'id="terminal"' in home
    assert "/static/js/terminal.js" in home
    assert "Coming soon" not in home
    assert "hero-reveal" in home
    assert 'class="terminal-chrome"' in home
    assert 'class="terminal-title"' in home
    assert "guest@bytebatallion: ~/terminal" in home


def test_home_js_boot_typing_rules():
    assert "bb_boot_seen" in HOME_JS
    assert "ACCESS GRANTED" in HOME_JS
    assert "hero-cursor" in HOME_JS
    assert "prefers-reduced-motion" in HOME_JS
    assert "finishBoot" in HOME_JS
    assert "Math.random()" in HOME_JS
    assert "getAttribute('aria-label')" in HOME_JS


def test_terminal_is_a_real_shell():
    assert "COMMANDS" in TERM_JS
    for cmd in (
        "pwd",
        "echo",
        "history",
        "date",
        "uname",
        "cd",
        "sudo",
        "exit",
    ):
        assert (cmd + ": {") in TERM_JS
    assert "Tab" in TERM_JS
    assert "complete()" in TERM_JS
    assert "'^C'" in TERM_JS or '"^C"' in TERM_JS
    assert "Last login" in TERM_JS
    assert "bb" in TERM_JS
    assert "bb flag" in TERM_JS
    assert "bb challenges" in TERM_JS
    assert "output.scrollTop = output.scrollHeight" in TERM_JS


def test_terminal_bb_gated_by_sign_in():
    assert "lines.push('  bb" not in TERM_JS
    assert "data-logged-in" in TERM_JS
    assert "sign in to use platform commands" in TERM_JS
    assert "not signed in" in TERM_JS
    assert "signed in" in TERM_JS
    assert "session-authenticated" in TERM_JS
    assert "never print another user" in TERM_JS
    home = TestClient(app).get("/").text
    assert 'data-logged-in="false"' in home
    assert 'data-username="guest"' in home


def test_notif_dot_hides_when_read():
    assert ".notif-dot[hidden]" in CSS
    assert "[hidden] {" in CSS
    assert "display: none !important" in CSS
    assert "dot.style.display = 'none'" in NOTIF_JS
    assert "dot.style.display = ''" in NOTIF_JS
    assert "onNotificationsPage" in NOTIF_JS
    assert "navLink.addEventListener('click', hideDot)" in NOTIF_JS


def test_css_terminal_float_and_hidden_scrollbars():
    assert "@keyframes terminal-float" in CSS
    assert "animation: terminal-float 6s ease-in-out infinite" in CSS
    assert "rgba(0, 255, 65, 0.12)" in CSS
    assert "scrollbar-width: none" in CSS
    assert "-ms-overflow-style: none" in CSS
    assert "::-webkit-scrollbar" in CSS
    assert "overflow-x: hidden" in CSS
    assert "flex-direction: column" in CSS
    assert ".terminal-chrome" in CSS
    assert ".term-btn" in CSS


def test_css_centred_hero_and_reduced_motion():
    assert ".hero {\n  text-align: center;" in CSS
    assert "clamp(1.8rem, 6vw, 2.8rem)" in CSS
    assert "overflow-wrap: break-word" in CSS
    assert "margin: 0 auto 24px" in CSS
    assert "margin: 24px auto 0" in CSS
    reduced = CSS.split("@media (prefers-reduced-motion: reduce)")[1]
    assert "#boot-overlay" in reduced
    assert ".terminal" in reduced
    assert ".hero-cursor" in reduced
    assert "animation: none" in reduced


# --- Terminal Virtual Filesystem & Warm-up Challenge ------------------------


def test_no_02_prefix_in_terminal_rules():
    assert "[02] Every player gets their own flag" not in TERM_JS
    assert (
        "Every player gets their own flag; sharing one never works" in TERM_JS
    )
    for n in range(1, 10):
        assert f"'[0{n}]" in TERM_JS
    assert "'[10]" in TERM_JS


def test_rules_txt_has_random_sample_flag():
    m = re.search(
        r"Sample flag \(format reference only[^']*?(BB\{[0-9a-f]+\})", TERM_JS
    )
    assert m, "rules.txt has no sample flag"
    assert re.fullmatch(r"BB\{[0-9a-f]{12}\}", m.group(1))


def test_readme_challenge_solves_to_flag_and_flag_not_leaked():
    groups = re.findall(r"\b[01]{8}\b", TERM_JS)
    assert len(groups) == 23, f"expected 23 bytes, got {len(groups)}"
    shifted = "".join(chr(int(b, 2)) for b in groups)
    decoded = _caesar(shifted, -12)
    assert FLAG_RE.fullmatch(decoded), decoded
    assert decoded not in TERM_JS
    assert "Twelve stand guard. Twelve steps." in TERM_JS
    assert "printLines(README)" in TERM_JS


def test_flag_txt_is_base64_of_flag():
    m = re.search(
        r"flag\.txt'\) printLine\('([A-Za-z0-9+/=]+)'\)", TERM_JS
    )
    assert m, "flag.txt handler has no base64 string"
    assert FLAG_RE.fullmatch(base64.b64decode(m.group(1)).decode())
    assert "nice try" not in TERM_JS


# --- UI Enhancements, Solved State, Next Redirects & Notifications ----------


def test_board_marks_solved_challenges_green():
    make_db_user("admin1", role="admin")
    solved_id = _make_ui_challenge("Solved One")
    unsolved_id = _make_ui_challenge("Unsolved One")
    player = TestClient(app)
    register(player, "alice_ui")
    r = _solve_ui_challenge(player, solved_id)
    assert r.status_code == 303
    assert "result=correct" in r.headers["location"]

    board = player.get("/challenges").text
    assert board.count("chal-card solved") == 1
    assert "[SOLVED]" in board
    solved_pos = board.index("Solved One")
    unsolved_pos = board.index("Unsolved One")
    assert "chal-card solved" in board[max(0, solved_pos - 200) : solved_pos + 200]
    assert "[SOLVED]" in board[solved_pos : solved_pos + 200]
    assert (
        "chal-card solved"
        not in board[max(0, unsolved_pos - 200) : unsolved_pos + 200]
    )
    assert "[SOLVED]" not in board[unsolved_pos : unsolved_pos + 200]


def test_custom_category_shows_in_profile_graph():
    make_db_user("admin1", role="admin")
    cid = _make_ui_challenge("Hardware Hack", category="hardware")
    player = TestClient(app)
    register(player, "bob_ui")
    _solve_ui_challenge(player, cid)

    profile = player.get("/profile").text
    assert 'data-cat="hardware"' in profile
    assert "Hardware — 1 (100.0%)" in profile
    assert "badge-cat-hardware" in profile


def test_flag_examples_use_bb_prefix():
    admin = make_admin_client()
    form = admin.get("/admin/challenges/new").text
    assert "BB{y0u_f0und_1t}" in form
    assert "BYTEBATTALION{" not in form

    make_db_user("admin2", role="admin")
    cid = _make_ui_challenge("Flag Format Check")
    player = TestClient(app)
    register(player, "carol_ui")
    detail = player.get(f"/challenges/{cid}").text
    assert 'placeholder="BB{...}"' in detail
    assert "BYTEBATTALION{" not in detail

    from app.flag_engine import validate_challenge_flag

    assert validate_challenge_flag("BB{something}")[0]
    assert validate_challenge_flag("BB{test_flag_value}")[0]


@pytest.mark.parametrize(
    "path",
    [
        "/challenges",
        "/profile",
        "/scoreboard",
        "/notifications",
        "/admin/submissions",
    ],
)
def test_anonymous_pages_redirect_to_login_with_next(path):
    anon = TestClient(app)
    r = anon.get(path, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/auth/login?next=")
    assert quote(path, safe="") in r.headers["location"]


def test_anonymous_top10_json_redirects_to_login():
    anon = TestClient(app)
    r = anon.get("/scoreboard/top10.json", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/auth/login?next=")


def test_login_post_with_next_query_param_redirects_there():
    player = TestClient(app)
    register(player, "dave_ui")
    client = TestClient(app)
    r = client.post(
        "/auth/login?next=/challenges",
        data={
            "csrf_token": get_csrf(client),
            "username": "dave_ui",
            "password": "password123",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/challenges"


def test_login_post_with_next_hidden_field_redirects_there():
    player = TestClient(app)
    register(player, "erin_ui")
    client = TestClient(app)
    r = client.post(
        "/auth/login",
        data={
            "csrf_token": get_csrf(client),
            "username": "erin_ui",
            "password": "password123",
            "next": "/scoreboard",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/scoreboard"


def test_login_post_with_unsafe_next_falls_back_to_challenges():
    player = TestClient(app)
    register(player, "frank_ui")
    client = TestClient(app)
    r = client.post(
        "/auth/login",
        data={
            "csrf_token": get_csrf(client),
            "username": "frank_ui",
            "password": "password123",
            "next": "https://evil.example/x",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/challenges"


def test_notifications_page_and_unread_json():
    admin = make_admin_client()
    db = SessionLocal()
    try:
        admin_user = db.query(User).filter_by(role="admin").first()
        from app.models import utc_now

        db.add(
            Announcement(
                title="Welcome",
                body="Event on Friday.",
                created_by=admin_user.id,
                created_at=utc_now(),
            )
        )
        db.commit()
    finally:
        db.close()

    player = TestClient(app)
    register(player, "heidi_ui")
    assert "Welcome" not in player.get("/").text
    assert "Welcome" not in player.get("/challenges").text
    page = player.get("/notifications")
    assert page.status_code == 200
    assert "Welcome" in page.text and "Event on Friday." in page.text
    data = player.get("/notifications/unread.json?seen=0").json()
    assert data["unread"] == 1
    assert data["latest"]["title"] == "Welcome"
    assert data["latest_id"] == data["latest"]["id"]
    data = player.get(
        f"/notifications/unread.json?seen={data['latest_id']}"
    ).json()
    assert data["unread"] == 0
    assert player.get("/notifications/unread.json?seen=nope").json()["unread"] == 1


def test_notifications_empty_state():
    player = TestClient(app)
    register(player, "ivan_ui")
    page = player.get("/notifications").text
    assert "No notifications yet." in page
    data = player.get("/notifications/unread.json").json()
    assert data == {"unread": 0, "latest": None, "latest_id": 0}


def test_home_page_terminal_tagline_logo_footer():
    player = TestClient(app)
    register(player, "judy_ui")
    home = player.get("/").text
    assert "Be smart enough to get the flag" in home
    assert "no shortcuts. no handouts. just you vs. the box." not in home
    assert "Coming soon" not in home
    assert "every player gets their own flag" not in home
    assert 'id="terminal"' in home
    assert 'data-username="judy_ui"' in home
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
    register(player, "kyle_ui")
    r = player.get("/challenges", follow_redirects=False)
    assert r.headers.get("cache-control") == "no-store, no-cache"
    r = player.get("/scoreboard/top10.json", follow_redirects=False)
    assert "no-store" not in r.headers.get("cache-control", "")
