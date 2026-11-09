# UI Changes — ByteBatallion (rename + hacker-style redesign)

> Note: this project was previously named **UPSIFS-CTF** (flag prefix
> `UPSIFS{...}`). It was renamed to **ByteBatallion** — see the rename map
> below. No other trace of the old name remains in code, templates, CSS,
> JS or docs, except dummy test email addresses (`@upsifs.in`), which are
> not user-visible branding and were intentionally left alone.

## 1. Rename map (UPSIFS-CTF → ByteBatallion)

| Before | After |
|---|---|
| Project folder `UPSIFS-CTF/` | `ByteBatallion/` |
| `UPSIFS-CTF Master Prompt.md` | `ByteBatallion Master Prompt.md` (content updated) |
| Display name `UPSIFS-CTF` | `ByteBatallion` (full title: `ByteBatallion - CTF Platform`) |
| Flag prefix `UPSIFS{...}` | `BYTEBATTALION{...}` (examples, placeholders, docs, comments, tests, smoke test) |
| `README.md`, `Dockerfile`, `requirements.txt` headers | `ByteBatallion` |
| `docker-compose.yml` `container_name: upsifs-ctf` | `container_name: bytebatallion` |
| `.env.example` / `app/config.py` DB path `data/upsifs_ctf.db` | `data/bytebatallion.db` |
| `app/main.py` FastAPI title | `ByteBatallion - CTF Platform` |
| `app/models.py` / `app/routes/admin.py` default `platform_name` | `ByteBatallion` |
| `app/security.py` session salt `upsifs-ctf-session` | `bytebatallion-session` (download salt likewise renamed) |
| `app/__init__.py` docstring, `app/flag_engine.py` comments/examples | `ByteBatallion` / `BYTEBATTALION{...}` |
| Templates (titles, footer, index tagline) | `ByteBatallion`; "UPSIFS Lucknow" removed, replaced with "Capture The Flag Platform" |
| Tests asserting the old name/flag (`test_phase0`, `test_phase5` save_config default, flag regexes, `test_bytebatallion.db`) | Updated to the new name/flag so the suite still passes |
| `app/main.py` log name `upsifs-ctf.error` | `bytebatallion.error` |

No Python logic, routes, DB schema, flag-engine rules, auth, scoring or
form field names were changed — this was a naming + visual pass only.

## 2. Full-screen fix

- The old `--max-width: 1040px` cap is gone (`--max-width: none`).
- `.cyber-nav`, `main`, `.footer-content`, `.admin-section`, `.detail-card`,
  `.profile-stats-strip` are all `width: 100%` with `24px 32px` padding on
  desktop (`16px` on mobile). No centred narrow column remains.
- Challenge board grid: `repeat(auto-fill, minmax(280px, 1fr))`.
- Scoreboard / profile / settings grids stretch full width.
- Auth cards stay a readable `max-width: 560px`, left-aligned inside the
  full-width main (not centred in a narrow page).

## 3. Hacker / terminal style rules applied

- Colours: pure black `#050505` background, `#0a0a0a` panels, `1px` hard
  borders `#1e1e1e` / dim green `#14532d`. One accent: phosphor green
  `#00ff41`. Secondary only amber `#ffb000` (warnings / first blood) and
  red `#ff3131` (danger / fails). Text `#d4d4d4`, muted `#737373`.
- No gradients, no `backdrop-filter` blur, no `box-shadow` glow, no
  `border-radius` (0 everywhere — no pills, no circular avatars/dots).
- No Inter / sans font: JetBrains Mono + Fira Code only, monospace body,
  uppercase mono labels with letter-spacing.
- No pulse/glow animations — the single exception is the blinking block
  cursor next to the brand (`cursor-blink`).
- No purple/blue/pink/orange category rainbow: every category badge is the
  same grey/green square tag; solved = green, first blood = amber,
  danger = red.
- Header: full-width top bar, hard `1px` bottom border, brand
  `[ ByteBatallion ] >_` in mono; nav links uppercase mono, active state is
  green text + green left border with `[ ]` markers, no pill background.
- Footer: full-width hard-bordered status line —
  `> bytebatallion -- status: ONLINE | per-user flags: ENABLED`.
- Section headings: `[01] // CHALLENGES`, `[02] // SCOREBOARD`,
  `[03] // PROFILE`, `[04] // ADMIN`, `[05] // ACCOUNT SETTINGS`,
  `[06] // TEAMS`.
- Hero (`index.html`): left-aligned, big mono `BYTEBATTALION` (plain, no
  gradient), a `>_` prompt line (`./bytebatallion --capture-the-flag`),
  one-line description "Capture The Flag Platform — every player gets
  their own flag.", countdown/notice as a plain bordered terminal line,
  announcements as square bordered blocks.
- Cards/buttons/tables/forms: square `1px`-border flat panels, no shadow;
  hover = green border + `#0d0d0d` background only (no `translateY`, no
  glow). Buttons: square, black background, `1px` green border, green mono
  text; hover inverts (green background, black text). Inputs: square, black
  background, `1px #2a2a2a` border, green focus border, no glow ring.
  Tables: square, hard `1px` borders, uppercase mono grey header, row hover
  `#0d0d0d` only. Badges: square, `1px` border, mono uppercase.
- The `body::before` scanline overlay was removed entirely; background is
  plain `#050505`.
- Profile: square avatar (not circle/hexagon, no gradient), stats as square
  bordered boxes, no gradient hero panel, left-aligned hero content.
- Charts: `scoreboard.js` and `profile_charts.js` palettes are now
  greens/greys/amber/red only; the profile area-chart gradient fill and
  line-glow filter were removed (flat green fill at low opacity, flat green
  line). `profile.html` category colours (`cat_colors`) are now
  greens/greys/amber only; donut strokes are `#00ff41` (solves) /
  `#ff3131` (fails).
- `board.html`: the bell emoji (🔔) is now `[!]` text. All templates were
  checked for emojis — none remain.
- All class names, template block names, element IDs, form names, URLs and
  JS hooks are unchanged, so existing tests and JS keep working.

## Files touched

- Renamed: project folder, `ByteBatallion Master Prompt.md`
- `README.md`, `Dockerfile`, `docker-compose.yml`, `.env.example`,
  `requirements.txt`
- `app/__init__.py`, `app/config.py`, `app/create_admin.py`,
  `app/flag_engine.py`, `app/main.py`, `app/models.py`, `app/security.py`,
  `app/routes/admin.py`
- `app/static/css/style.css` (full rewrite, same class names)
- `app/static/js/scoreboard.js`, `app/static/js/profile_charts.js`
- `app/templates/base.html`, `app/templates/index.html`,
  `app/templates/challenges/board.html`, `app/templates/scoreboard.html`,
  `app/templates/profile.html`, `app/templates/settings.html`,
  `app/templates/teams.html`, `app/templates/admin/base.html`,
  plus brand/flag text in `auth/login.html`, `auth/register.html`,
  `challenges/detail.html`, `admin/challenge_form.html`,
  `admin/import.html`
- `scripts/smoke_test.py`, `tests/conftest.py`, `tests/test_phase0.py`,
  `tests/test_phase2.py`, `tests/test_phase3.py`, `tests/test_phase4.py`,
  `tests/test_phase5.py`, `tests/test_phase6.py`, `tests/test_phase7.py`
- This file: `UI-CHANGES.md`
- `data/challenges/.gitkeep`, `data/instances/.gitkeep` (new — the zip
  shipped without a `data/` directory, and both `pytest` and `run.py`
  fail with `sqlite3.OperationalError: unable to open database file`
  without it; the `.gitignore` already expected these two dirs to exist)

## How to run it

```bash
cd ByteBatallion
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set a real SECRET_KEY
python run.py
# open http://127.0.0.1:8000
```

Tests:

```bash
python3 -m pytest tests/ -q
```

---

# v2 — improvements (2026-11-09)

Display name is now **`BYTE_BATALLION_CTF_PLATFORM`** (was `ByteBatallion`);
flag format is now **`BB{...}`** (was `BYTEBATTALION{...}`). Rename note:
`BYTEBATTALION{...}` → `BB{...}` in every example, placeholder, doc,
comment, test and the smoke test. **Stored challenges keep their old
flag/prefix** — a challenge already saved in an existing DB keeps its
stored flag and still solves exactly as before; only new challenges
should use `BB{...}`. The flag engine itself validates any
`PREFIX{...}` generically and was not changed.

## 1. Solved challenges green on the board

- `board()` passes `solved_ids` (challenge ids the current user has a
  `Solve` row for) to `challenges/board.html`; a solved card gets class
  `solved` + a green `[SOLVED]` badge.
- CSS `.chal-card.solved`: green 1px border, dark-green tinted
  background (`#04140a`), title in phosphor green. Still square/flat.
- The detail page's own solved badge is unchanged.

## 2. Custom categories in the profile graph

- The profile donut + legend already grouped by the real stored
  category, so a custom category (e.g. `hardware`) just works — verified
  by a real test (`tests/test_v2_improvements.py`).
- `cat_colors` expanded from 8 to 12 shades (greens/greys/amber only,
  no rainbow), so more distinct categories get distinct segments.
- The legend now shows `name — count (pct%)` for every category.
- The generic `.badge-cat` style covers arbitrary category names in the
  solves log (no per-category rule needed).

## 3. Flag format `BB{...}`

- Every `BYTEBATTALION{...}` example/placeholder/doc/comment/test
  replaced with `BB{...}` (admin challenge form, bulk-import placeholder,
  challenge detail placeholder, `flag_engine.py` + `models.py` comments,
  README, smoke test, master prompt, all tests). No code hardcodes a
  prefix — grep `BYTEBATTALION` afterwards shows only the historical
  rename map in this file.

## 4. Logical back-button / navigation mapping

- `app/security.py`: `_safe_next()` (relative path starting with `/`,
  never `//`), `login_url()` (login URL honouring a safe `next`),
  `login_redirect()` (login URL with `next` = current page),
  `safe_next_or()` (resolve `next` from a form field or the query
  string, else a fallback). `require_user` and anonymous
  `require_admin` now redirect to login with `next`; logged-in
  non-admins still get 403. The download route does the same instead of
  a bare 403.
- Login/register POSTs honour a safe `next` (hidden field in both forms,
  preserved through the login form) and redirect there (303); without
  `next` they go to `/challenges`. Logged-in users hitting GET
  `/auth/login` or `/auth/register` go to `/challenges`.
- PRG for every POST handler: all end in a 303, never a 200
  TemplateResponse. Error/success messages travel in the query string
  and the GET page renders them: auth login errors
  (`/auth/login?error=...&username=...`), register errors
  (`/auth/register?error=...&username=...&email=...`), settings
  (`/settings?msg=...` / `?error=...`), admin challenge create/edit
  (back to the form URL with `?error=...`), bulk-import preview
  (`/admin/challenges/import?items=<json>&errors=...`; parse failure
  goes back with `?error=...`), custom-page create/edit (same pattern),
  user password reset
  (`/admin/users?msg=...&new_password=...&new_password_for=...`).
  **Field values are lost on the error redirects** for the admin
  challenge/page forms — the admin re-types them; this is accepted, not
  a bug.
- Middleware sets `Cache-Control: no-store, no-cache` on HTML responses
  for logged-in users (static paths and `application/json` skipped), so
  Back shows fresh state (a just-solved challenge already green).
- Challenge detail pages have a `[<] BACK TO CHALLENGES` link at the top.

## 5. Login guard everywhere

- Every login-required page redirects anonymous users to `/auth/login`
  (with `next`) instead of showing content or a bare 403: challenges,
  submit, scoreboard, profile, settings, teams, admin, download routes,
  `/notifications`, `/scoreboard/top10.json`. Public pages stay public:
  `/`, `/auth/login`, `/auth/register`, published `/pages/<slug>`.

## 6. Admin submission logs + PDF export

- `/admin/submissions` is unchanged (list + filters). New admin-only
  route `GET /admin/submissions/export.pdf` returns a real PDF
  (`application/pdf`, attachment `bytebatallion-submissions.pdf`) built
  with reportlab platypus (SimpleDocTemplate + Paragraph/Table, default
  fonts): title `BYTE_BATALLION_CTF_PLATFORM — Submission Log`,
  generated-at UTC timestamp, summary (total / correct / incorrect /
  unique users / unique challenges), a per-challenge breakdown table,
  then the full log (time UTC, user, challenge, submitted-hash first 12
  chars, result) — capped at the latest 1000 rows, with a note when the
  cap bites. Plaintext flags are NEVER stored, so the PDF shows hash
  prefixes only — stated in the PDF and on the submissions page.
- `reportlab==4.2.5` added to `requirements.txt`. "Export PDF" button on
  the submissions page; link on the export page. Anonymous → login
  redirect; logged-in non-admin → 403.

## 7. Notifications — separate tab + page

- Announcements are **removed from the home page and the challenge
  board entirely** (queries removed from `main.py home()` and
  `challenges.py board()` too). They live on the new Notifications page.
- `app/routes/notifications.py`: `GET /notifications` (login required,
  all announcements newest-first, empty state "No notifications yet.")
  and `GET /notifications/unread.json?seen=<max id>` (login required;
  non-int `seen` tolerated, defaults to 0) returning
  `{"unread": n, "latest": {...} | null, "latest_id": int}`.
- Top nav (logged-in users only): a `Notifications` tab with a red dot
  `<span class="notif-dot" id="notif-dot" hidden>` — a solid red square,
  no pulse animation; the count shows as text inside the dot when > 0.
- **No DB migration:** unread tracking is client-side
  (`app/static/js/notifications.js`, loaded with `defer` in `base.html`).
  It reads `bb_seen_notif` from localStorage, polls
  `/notifications/unread.json` on load and every 60s, shows/hides the
  dot, and — once per session per announcement
  (sessionStorage `bb_toasted_<id>`) — shows a bottom-right toast
  (`.notif-toast`: square black box, 1px green border, mono text, close
  button, auto-hides after 8s) and plays a short beep via a WebAudio
  oscillator. Visiting `/notifications` sets the seen id to the latest
  and hides the dot.
- **WebAudio autoplay limitation:** browsers block sound until the user
  interacts with the page, so the beep is attempted on load and — if the
  AudioContext is still suspended — re-attempted on the first
  pointerdown/keydown (one-time listener). The first toast on a fresh
  tab may therefore be silent until the user clicks or presses a key.

## 8. Logo + rename

- The uploaded logo was copied to `app/static/img/logo.png`: near-white
  pixels (all channels > 240) converted to transparent with PIL
  (pillow was used as a one-off conversion tool only — it is **not**
  in `requirements.txt`), master resized to **150x150** and saved as PNG.
- It is displayed **small in the header — 40x40px** via CSS
  `.brand-logo`. The file is 150x150, but 150px in the nav bar would
  break the layout, so the display size is 40px.
- Display name renamed everywhere from `ByteBatallion` to
  `BYTE_BATALLION_CTF_PLATFORM`: FastAPI title, `models.py` +
  `admin.py` config default `platform_name`, template `<title>` blocks
  (cfg name variable where the template already has it, otherwise the
  literal new name), README, Dockerfile comment, smoke test, tests.
  The flag format stays `BB{...}`. Internal identifiers are unchanged:
  folder `ByteBatallion/`, DB `bytebatallion.db`, session salt
  `bytebatallion-session`, CSS/JS comments, `app/__init__.py` and
  `flag_engine.py` docstrings.

## 9. Home page

- Tagline replaced: `> Be smart enough to get the flag.` plus
  `// no shortcuts. no handouts. just you vs. the box.`
- The "Coming soon. The challenge board opens before the event." branch
  is gone entirely. The before-start countdown and the ended notice stay.
- New **active terminal** (`app/static/js/terminal.js`, `.terminal` in
  `style.css`): 100% width, max-width 880px, 320px tall, left-aligned,
  monospace, black background, 1px green border **with a subtle green
  glow** (`box-shadow: 0 0 18px rgba(0,255,65,0.25)`) — the one place
  glow is allowed, because the user explicitly asked for it here.
  Prompt is `<username>@bytebatallion:~$` (logged-in username, else
  `guest`); typed input with a blinking block cursor; history with
  ArrowUp/ArrowDown; clicking the terminal focuses the input. Commands:
  `help`, `ls` (`rules.txt  README.txt  flag.txt`), `cat rules.txt`
  (the full ~10 rules: flag format `BB{...}`; every player gets their
  own flag, sharing never works; no attacking the platform / DoS /
  login brute-force; no flag sharing or asking for flags; solve it
  yourself, hints cost points; respect each challenge's scope; play
  fair, first blood earns glory; breaking the platform = ban),
  `cat README.txt` (one-line platform intro), `cat flag.txt`
  (`nice try. the flag is earned, not catted.`), `whoami`, `clear`,
  and unknown commands print
  `command not found: <cmd> — type 'help'.`

## 10. Footer

- Footer-left is now
  `> BYTE_BATALLION_CTF_PLATFORM -- status: ONLINE | Made by Hackerzzz`
  (rendered from the cfg name variable, so an admin-renamed platform
  still renders correctly). `per-user flags: ENABLED` is gone.

## Files touched (v2)

- `app/security.py`, `app/main.py`, `app/models.py`,
  `app/routes/challenges.py`, `app/routes/auth.py`,
  `app/routes/settings.py`, `app/routes/admin.py`,
  `app/routes/notifications.py` (new), `app/routes/scoreboard.py`
  (unchanged — category grouping already used the stored category)
- `app/templates/base.html`, `app/templates/index.html`,
  `app/templates/challenges/board.html`,
  `app/templates/challenges/detail.html`,
  `app/templates/auth/login.html`, `app/templates/auth/register.html`,
  `app/templates/profile.html`, `app/templates/settings.html`,
  `app/templates/scoreboard.html`,
  `app/templates/admin/challenge_form.html`,
  `app/templates/admin/import.html`,
  `app/templates/admin/submissions.html`,
  `app/templates/admin/export.html`,
  `app/templates/notifications.html` (new)
- `app/static/css/style.css`, `app/static/js/notifications.js` (new),
  `app/static/js/terminal.js` (new), `app/static/img/logo.png` (new)
- `requirements.txt` (reportlab), `README.md`,
  `tests/test_v2_improvements.py` (new),
  `tests/test_phase0.py`, `tests/test_phase1.py`,
  `tests/test_phase2.py`, `tests/test_phase5.py`,
  `tests/test_phase6.py`, `tests/test_phase7.py`,
  `tests/test_settings_and_profile_ui.py`
  (updated for the new redirects / flag prefix / display name)

## v2.1 — home hero tweak (2026-11-09)
- Home page hero heading now renders `BYTE_BATALLION` only: `index.html` strips the
  `_CTF_PLATFORM` suffix from the configured platform name
  (`{{ cfg.name | replace('_CTF_PLATFORM', '') }}`). The full name
  `BYTE_BATALLION_CTF_PLATFORM` stays everywhere else (nav brand, footer,
  page titles, FastAPI title). If an admin renames the platform in
  /admin/config, the hero shows the new name as-is.

## v2.2 — home motion (2026-11-09)
- Skill consulted: `ui-ux-pro-max` (upstream nextlevelbuilder/ui-ux-pro-max-skill).
  Verified match: `cyberpunk-ui` + `dark-mode-oled` — neon green on deep black,
  monospace, minimal glow, terminal aesthetic. This CONFIRMED the existing
  tokens, so no colours or fonts were changed. Motion/a11y rules applied from
  the skill: distinct durations per motion (boot lines 150ms, typing ~65ms/char
  with 40–90ms jitter, reveal 400ms, boot fade 400ms, terminal float 6s);
  transform/opacity only, never width/height; `prefers-reduced-motion` always
  honoured (final state renders immediately); looping motion pauses while the
  tab is hidden (`visibilitychange` → `.page-hidden`); no horizontal scroll on
  mobile (hero h1 uses `clamp()` + `overflow-wrap`).
- Boot overlay (`#boot-overlay`, home only): full-screen black overlay with
  fast-printing hacker boot lines, a `[####------] 68%` progress bar and
  `> ACCESS GRANTED — welcome, hacker`, then a 400ms fade-out and removal
  from the DOM. Total ~2s max. Click or any keypress skips it. Shown only
  ONCE per browser session (`sessionStorage bb_boot_seen`). The markup ships
  with the `hidden` attribute and home.js drives it, so a JS failure can
  never block the page.
- Hero typing — OVERRIDE of v2.1: v2.1 stripped `_CTF_PLATFORM` from the hero
  heading, but the user explicitly asked for the big text
  `BYTE_BATALLION_CTF_PLATFORM` to type out letter by letter, so the v2.1
  `replace('_CTF_PLATFORM', '')` was REVERTED. The h1's static HTML now holds
  the full `{{ cfg.name }}` (with an aria-label of the full name, so no-JS
  users and screen readers still get it); home.js clears it and types the
  full name one letter at a time, with a blinking underscore `_` cursor that
  keeps blinking after typing finishes. After typing, the taglines/notice/
  terminal fade/slide in staggered (~120ms apart). `prefers-reduced-motion`:
  no typing, full text shown at once.
- Home content centred (home hero ONLY — no other page touched):
  `.hero { text-align: center; }`, notice `margin: 0 auto 24px` (max-width
  720px), terminal `margin: 24px auto 0`.
- Terminal fixes: visible scrollbars removed (bottom and side) via
  `scrollbar-width: none`, `-ms-overflow-style: none` and
  `::-webkit-scrollbar { width: 0; height: 0; display: none; }` on both
  `.terminal` and `.terminal-output` — the terminal stays scrollable and
  terminal.js still auto-scrolls (`scrollTop = scrollHeight`). Floating
  animation: `terminal-float` keyframes (`translateY(-6px)`, 6s ease-in-out
  infinite, transform-only). Glow softened to very light:
  `box-shadow: 0 0 24px rgba(0, 255, 65, 0.12)` (was 0.25). Reduced motion
  disables the float.
- New files: `app/static/js/home.js`, `tests/test_home_animation.py`.
  Files touched: `app/templates/index.html`, `app/static/css/style.css`.

## v2.3 — home text + realistic terminal + red-dot fix (2026-11-09)
- Hero typing REVERTED to v2.1 behaviour: the typed/static hero text is
  `BYTE_BATALLION` only — the `replace('_CTF_PLATFORM', '')` filter is back
  on the h1 (aria-label and text), so home.js types the short name (it
  types whatever the aria-label holds, so the two can never drift apart).
  The full name stays in nav/footer/titles. This reverses the v2.2
  override, at the user's request.
- Home text: the `// no shortcuts. no handouts. just you vs. the box.`
  tagline was removed. `./bytebatallion --capture-the-flag` and
  `> Be smart enough to get the flag.` stay.
- Terminal, made realistic (v2.3): a window-chrome bar was added on top of
  the terminal (three square buttons + `guest@bytebatallion: ~/terminal` +
  `[ bash ]`), green monochrome per the core style. The terminal is now a
  flex column — the chrome stays pinned at the top and only the output area
  scrolls (scrollbars still hidden). It is a working mini-shell:
  login banner (`Last login: ... from 127.0.0.1`), a prompt that follows
  the current directory (`guest@bytebatallion:~/challenges$`), a virtual
  filesystem (`~`, `/`, `challenges`), and commands `help`, `ls` (incl.
  `ls -la`), `pwd`, `cd`, `cat`, `echo`, `whoami`, `date`, `uname -a`,
  `history`, `clear`, `sudo` and `exit` (the last two answer in character),
  plus ArrowUp/Down history, Tab completion (commands + files), Ctrl+C
  (cancel line, prints `^C`) and Ctrl+L (clear).
- Terminal, future challenge/flag use: every command lives in one
  `COMMANDS` registry in `terminal.js` (name -> {desc, run}) — adding a
  command is one entry, no other file changes. The `bb` namespace is
  reserved for the platform shell: `bb help` / `bb status` work now;
  `bb challenges` and `bb flag <name>` are honest stubs that say exactly
  what their future handler needs (a signed-in session calling the
  platform). For now flags are still submitted on the challenge page.
- Red-dot fix (the actual bug): the notification dot stayed red FOREVER
  because `.notif-dot { display: inline-block }` beats the browser's
  built-in `[hidden] { display: none }` — so `dot.hidden = true` in JS
  did nothing visually. Fixed in three layers: (1) CSS guard
  `.notif-dot[hidden] { display: none; }` plus a global
  `[hidden] { display: none !important; }`; (2) `hideDot()` in
  notifications.js also sets `dot.style.display = 'none'` (and
  `showDot()` clears it); (3) the dot hides the instant the
  /notifications page loads (before the unread fetch returns) and the
  instant the Notifications nav link is tapped — the page load then marks
  everything seen via `bb_seen_notif` as before. The dot now only shows
  when there is a genuinely unread announcement.

## v2.4 — terminal `bb` security review + sign-in gate (2026-11-09)
- Question asked: are the `bb` options in the terminal available to a
  normal user too, and can they expose anything?
- Finding (verified in the code, not assumed): NO exposure today. The
  terminal is 100% client-side JavaScript — it makes zero network calls
  (no `fetch`, no XHR), so it cannot reach the server, the database, any
  flag, or any other user's data. The `bb` subcommands are text stubs
  that only print fixed strings. The only values `bb status` prints are
  the visitor's own username (already shown in the nav bar when signed
  in; `guest` when anonymous) and the flag format `BB{...}` (public —
  printed by `cat rules.txt` on the same page). `sudo`/`exit` are jokes.
- Fixed anyway, because the design invited the worry:
  1. `help` listed `bb` TWICE (once from the COMMANDS registry, once
     from a hardcoded extra line) — the hardcoded line is removed; the
     registry entry is now the only one.
  2. The home page is public, so `bb` looked like a platform shell open
     to everyone, including anonymous visitors. The terminal now gets
     `data-logged-in` from the template (`request.state.user`, never
     guessed from the username — a real account could be named
     "guest"). `bb challenges` and `bb flag` now refuse anonymous users
     (`sign in to use platform commands — open /auth/login first.`);
     `bb status` shows `signed in` / `not signed in`; `bb help` stays
     public (it only describes the commands).
  3. A SECURITY block is now written at the top of `terminal.js` for
     whoever wires `bb` to real data later: any future handler MUST call
     a session-authenticated server endpoint, MUST refuse anonymous
     users, and MUST return only the caller's own data — per-user flags
     are stored as hashes, and a handler must never print another user's
     flag or any plaintext flag. The client-side gate is a UX guard only;
     the server endpoint must enforce the same gate again.

## v2.5 — THE BATALLION story section + footer rename + centred login (2026-11-09)
- New home-page section `#battalion`, below the terminal: header
  `[02] // THE BATALLION`, tagline `> Four operators. One standing
  operation.`, and three short story paragraphs (how four operators who
  kept meeting on the scoreboards formed the Batallion in 2026; one
  target, one scope, one flag per player; the Batallion watches the
  board and finds whoever climbs it). Structure inspired by DeadFace's
  "Actors" page (intro story, then one card per character) — all story
  text and characters are original ByteBatallion lore, nothing copied.
- Four flip cards (HACKERP — Commander; C1PH3R — Cryptography; GH0ST —
  Forensics & OSINT; V01D — Web & Pwn), each with its generated green
  monochrome portrait (`app/static/img/characters/*.png`, 640x640).
  Front: portrait + handle + role + `[+] FLIP`. Back: Role / Specialty /
  Threat Level / bio / signature stat (`First Bloods: 27`, `Ciphers
  broken: 41`, `Cases reconstructed: 33`, `Shells popped: 58`) + `[x]
  BACK`. Click flips; cards are real `<button>`s so Enter/Space flip
  them natively, with `aria-pressed` toggling and an aria-label naming
  the character. CSS 3D flip (`preserve-3d`, `backface-visibility:
  hidden`, `transform 0.6s ease`, transform-only); a flipped card keeps
  its grid size, no layout shift. Grid is `auto-fit minmax(240px, 1fr)`
  (max 4 across, 1 column on mobile), no horizontal scroll.
- Sync with the initial animation: the same entrance language as the
  hero — nothing hidden until `story.js` adds `.js-anim` (no-JS users
  see everything), then opacity + translateY 400ms, staggered 120ms per
  item, via IntersectionObserver when the section scrolls into view
  (immediately if already in view). `prefers-reduced-motion`: no reveal
  animation and no flip animation (instant swap).
- Footer rename: `Made by Hackerzzz` → `Made by HackerP` (base.html;
  the only user-visible occurrence — historical notes in this file keep
  the old name). Test updated (`test_home_page_terminal_tagline_logo_footer`).
- Login centering: `.auth-card { margin: 0 auto; }` (was `margin: 0`) —
  the login box is now centred on the page; the shared class means
  register centres too. The form itself stays left-aligned inside the
  box.
- New files: `app/static/js/story.js`, `tests/test_battalion.py`,
  `app/static/img/characters/*.png`. Files touched: `app/templates/
  index.html`, `app/templates/base.html`, `app/static/css/style.css`,
  `tests/test_v2_improvements.py`.

## v2.6 — 12 Batallion characters + hover flip + hacker cursor (2026-11-09)
- Batallion expanded from 4 to 12 cards (same sketch-style portraits,
  640x640, green monochrome — no regeneration). The original 4
  (HACKERP, C1PH3R, GH0ST, V01D) are unchanged; 8 new operators joined:
  R4ZOR (Malware Developer), S1REN (Social Engineer), N3TRUNN3R
  (Network Infiltrator), B1NARY (Reverse Engineer), V3N0M (Database
  Hunter), SH4D0W (Steganographer), F0RG3 (Hardware Hacker), Z3R0D4Y
  (Zero-day Hunter) — each with Role / Specialty / Threat Level / bio /
  signature stat on the back. Tagline now reads `> Twelve operators.
  One standing operation.` Story paragraphs unchanged. Grid unchanged
  (`auto-fit minmax(240px, 1fr)`, max 4 across).
- Flip behavior changed from click-only to hover: a card flips
  automatically when the cursor is over it (pure CSS `:hover`) and
  flips back when the cursor moves away. Keyboard focus flips it too
  (`:focus-within`). On touch devices (no hover) a tap toggles the flip
  — story.js gates its click handler on `(hover: none)` so the toggle
  can never fight the CSS hover state on mouse devices and leave a card
  stuck flipped; focus/blur keep `aria-pressed` in sync with the focus
  flip. Front hint changed to `[ HOVER / TAP TO REVEAL ]`. Same 0.6s
  transform-only flip; reduced motion still swaps instantly.
- Custom hacker cursor, site-wide (new `app/static/js/cursor.js`,
  included in base.html with `defer`): the native OS cursor is hidden
  and replaced by a phosphor-green square outline with a blinking
  center block, plus a smaller ghost dot trailing behind (lerped
  positions via requestAnimationFrame, transform-only). States:
  default (small square), hovering a link/button/card (bigger square +
  `>` prompt glyph), text input/textarea (I-beam). Safety: `cursor:
  none` applies ONLY when the script adds `.custom-cursor` to `<html>`
  (script failure = native cursor stays); cursor elements have
  `pointer-events: none` and never block clicks (terminal focus and
  buttons verified working); the whole thing is gated on
  `(hover: hover) and (pointer: fine)`, so touch devices never see it;
  `prefers-reduced-motion` gets a static cursor (no lag, no blink, no
  ghost). Static same-origin script, so CSP `script-src 'self'` still
  allows it — no inline script added. The terminal's own block cursor
  (`.term-cursor`) is separate and untouched.
- New files: `app/static/js/cursor.js`. Files touched:
  `app/templates/index.html`, `app/templates/base.html`,
  `app/static/css/style.css`, `app/static/js/story.js`,
  `tests/test_battalion.py`.

## v2.7 — centred Batallion + neon glow + green smoke + glowing cards (2026-11-09)
- Section centred: `.battalion { text-align: center; }` — heading,
  tagline and story all centred (story keeps max-width 720px + auto
  margins; card text stays left-aligned inside the cards).
- Neon glow on the heading: the name sits in its own
  `<span class="battalion-name">` so only `THE BATALLION` glows, not
  the `[02] //` prefix — green text-shadow with a gentle 3s live pulse
  (`battalion-glow` keyframes).
- Grid: exactly 3 cards per row on desktop (12 cards = 4 rows),
  2 per row on tablet (≥640px), 1 on mobile, grid centred
  (`justify-content: center`, 320px columns).
- Green smoke background (home page only): a fixed `.smoke-layer`
  with 3 big soft green blobs (`filter: blur(70px)`, opacity 0.05–0.07)
  drifting very slowly (`smoke-drift`, 36–52s, transform-only).
  The layer is `z-index: -1` + `pointer-events: none`, so it sits
  behind all page content and can never cover text or buttons or
  intercept clicks.
- Live neon glow on the character cards: `.flip-face` carries a green
  box-shadow that breathes (`card-glow`, 3.2s), wave-offset per column
  (0s / 0.6s / 1.2s). Very light corner curve: `border-radius: 6px`
  on the card, both faces and the portrait image top corners (an
  explicit exception to the site's square-corners rule, for the
  character cards only).
- Reduced motion: heading pulse, card glow pulse and smoke drift are
  all disabled (smoke stays put, faint as ever).

## v2.7.1 — smoke visibility fix (2026-11-09)
- The v2.7 smoke was invisible on the real page. Two causes: the layer
  sat at `z-index: -1` (fragile — anything with a background could cover
  it) and the opacity was ~7% over an already faint gradient (about 3%
  effective green — invisible on black). Fixed: the layer is now
  `z-index: 0` with the sticky header (100), main (1) and footer (1)
  explicitly layered above it, and the smoke is 3–4x stronger —
  gradient core `rgba(0, 255, 65, 0.85)`, blob opacities 0.22–0.34 —
  with a bigger, more visible drift (±10vw/±8vh, scale up to 1.25).
  A 4th blob (`.smoke-d`) was added as a brighter focal wisp behind the
  hero title. Still soft, still behind everything, still click-proof —
  now actually visible. Verified with a real browser screenshot this
  time, not just a code check.

## v2.8 — DeadFace card curve + pop + favicon + social icons + PDF export fix + per-user logs (2026-11-09)
- **Card corners are the DeadFace curve now.** `.flip-card-inner`,
  `.flip-face` and the portrait image went from 6px to **18px** — the
  same roundness as the reference card. Nothing else about the cards
  changed (same art, same flip, same glow).
  - Hidden catch, found by checking the live page (not just the CSS):
    the global reset `* { border-radius: 0 !important; }` was silently
    zeroing EVERY radius in the file — the cards computed to 0px, so
    the 6px curve in v2.7 never rendered either. The `!important` is
    dropped (a plain author-level `*` rule still beats UA radii); the
    cards and the social icons are now the only deliberate exceptions.
    A test locks this in (`border-radius: 0 !important` must not return).
- **Favicon = the ByteBatallion logo.** `base.html` links
  `rel="icon"` (+ `apple-touch-icon`) to `/static/img/logo.png` — the
  same image as the nav brand — so the browser tab shows the logo on
  every page.
- **Small continuous pop on the cards.** `.flip-card` runs a 4s
  `card-pop` loop (scale 1 → 1.02 → 1), wave-offset per column like
  the border glow. It animates the `scale` property, NOT `transform`,
  so it never fights the scroll-reveal slide or the flip rotation.
  Off under `prefers-reduced-motion`.
- **Instagram + LinkedIn under the cards.** A `[ FOLLOW THE BATALLION ]`
  row after the grid: two small (40px) green-neon icons side by side —
  inline SVG in the page's green glow style, opening in a new tab:
  Instagram https://www.instagram.com/byte__battalion and LinkedIn
  https://www.linkedin.com/company/byte-battalion/.
- **PDF export FIXED — it was 500ing.** The admin submission-log export
  passed challenge titles raw into reportlab `Paragraph`, which parses
  them as XML markup: a title like `A <b Bold` crashed the whole export
  (`paraparser: unclosed tags` → generic 500 page), and `<...>` fragments
  were silently swallowed from the PDF even when it didn't crash
  (`Web & Pwn <Basics>` rendered as just `Web & Pwn`). Fix: all dynamic
  text is XML-escaped before `Paragraph`; only our own fixed `<b>`
  headers use raw markup. Also fixed while in there: the "small" style
  was the SAME object as "Normal" (`small = styles["Normal"]` then
  mutating `fontSize`), which silently shrank every Normal paragraph to
  9pt — it is a real child style now. Verified live: admin login via
  curl → export downloads a real PDF (`200 application/pdf`,
  `attachment; filename=bytebatallion-submissions.pdf`, starts `%PDF-`),
  with a title containing `<b` and `&` in the data.
- **Export follows the filters.** The Export PDF link on the submissions
  page now carries the current user / challenge / result filters, and
  the export route accepts them — so an admin can download exactly the
  view they are looking at (a filtered export is named
  `bytebatallion-submissions-<user>.pdf` and notes the filter inside).
- **Per-user logs are one click now.** The submissions page's user
  filter is a dropdown of real usernames instead of a text box (exact
  match — the old substring match would have pulled `grace2` into
  `grace`'s evidence). Selecting a user shows an evidence summary strip:
  total / correct / incorrect submissions, challenges attempted, first
  and last activity, plus an "Export this user's PDF" link. The Users
  page has a "Submissions" action per user that jumps straight to that
  user's log.
- Tests: new `tests/test_v2_8.py` (PDF survives markup-looking titles
  and renders them whole; export respects the user filter; dropdown +
  summary + exact-match behaviour; Users-page link) and battalion
  tests extended (18px curve, pop keyframes, no `!important` reset,
  favicon, social links with the real URLs). Full suite: 128 passed.

## v2.9 — no "[02] //" prefix + terminal challenge files (2026-11-09)
- **The `[02] //` prefix is gone.** The Batallion heading is just
  `THE BATALLION` now (neon glow kept). In the terminal the old
  `[02]` rule marker is gone too: its text ("every player gets their
  own flag") is folded into rule [01], the rest renumbered so the
  list stays a clean [01]–[10]. (The scoreboard's `[02] // Scoreboard`
  heading was NOT touched — only the Batallion section + terminal.)
- **`cat rules.txt` hides a random flag.** Rule [10] is a sample flag
  in the platform format, for format reference only:
  `BB{2fd34a7795ef}` (random hex, not a real challenge flag).
- **`cat README.txt` is a real text-only challenge now** — crypto +
  binary decoding, no files, no links: a message scrambled in two
  layers (Caesar shift, then binary). The hint gives the shift away
  thematically — the number of operators in the Batallion (twelve).
  The flag is NOT in the file anywhere; the author holds it.
  Challenge flag: held by the author only — v0.1 scrubbed the
  plaintext answer out of this changelog and the tests, because a
  public repo must never contain a live challenge answer. Solved
  path, verified by script + by the test suite: binary → ASCII →
  Caesar-unshift by 12 → flag.
- **`cat flag.txt` gives a simple base64 flag.** The old "nice try"
  line is replaced by `QkJ7YmFzZTY0XzFzX2Vhc3l9` — a demo flag for
  the author to build a challenge around (decode it to read it; the
  plaintext is not written in this repo).
- `ls -la` sizes updated to match the real file contents (rules 628,
  README 622, flag 24). Terminal verified in a real browser: all
  three `cat` commands typed and read back, heading confirmed as
  `THE BATALLION`.
- Tests: new `tests/test_v2_9.py` (no [02] marker in the rules; the
  rules sample flag is a 12-hex `BB{...}`; the README binary decodes
  to a `BB{...}` flag with a -12 shift AND the decoded flag appears
  nowhere in `terminal.js`; `flag.txt` base64 decodes to a `BB{...}`
  demo flag), battalion heading test updated. Full suite: 132 passed.

## v0.1 — GitHub-ready release: full security audit + cleanup (2026-11-09)

- **Audit — what was checked and passed.** No secrets anywhere in
  the tree: no `.env`, no `.pem`/`.key`, no `.db`/SQLite files, no
  hardcoded password/token/API key (one grep hit was the literal
  string `<redacted>` in a comment). `config.py` ships only a
  dev-only default secret and `validate_settings()` refuses to boot
  in prod with it (or a short secret, or DEBUG on). `.env.example`
  holds placeholders only. Dependencies are all pinned in
  `requirements.txt`. Auth stack verified by reading the code:
  bcrypt passwords, signed HttpOnly session cookies (Secure in
  prod, 7-day expiry, user re-loaded per request so bans apply
  instantly), CSRF double-submit on every POST, login rate limit
  (5 fails → 15-min lockout, per IP+username), open-redirect guard
  on `?next=`, strict CSP (`script-src 'self'`, no inline scripts),
  HSTS in prod, branded error pages that leak nothing, flags stored
  as SHA-256 only, uploads sanitized + extension-blocklisted +
  50 MB capped. Dockerfile runs as non-root `appuser` and strips
  `.env` from the image; `.dockerignore` keeps the build context
  secret-free.
- **Audit — dependency vulnerabilities fixed (pip-audit).** The pinned
  set had three flagged packages: `python-multipart 0.0.20` (six
  PYSEC advisories), `pytest 8.3.5` (one) and `starlette 0.46.2`
  (eight, transitive via FastAPI). Bumped and re-verified with the
  full suite: `python-multipart 0.0.31`, `pytest 9.0.3`,
  `fastapi 0.162.2` + `starlette 1.10.0` (explicit pins for the
  audited set, with `pydantic 2.14.3` / `anyio 4.16.0`). Re-ran
  pip-audit after the bump: **no known vulnerabilities found**.
  No other pins changed.
  password reset put the new password in the redirect URL
  (`/admin/users?...&new_password=<password>`). Query strings land
  in browser history and in server access logs (uvicorn logs the
  full request path), so the old "never stored or logged" comment
  was wrong. Fix: `OneTimePasswordStore` in `app/security.py` —
  the redirect now carries only a random single-use token (5-min
  TTL); the users page pops the password once and a refresh shows
  nothing. The existing reset test now asserts the redirect URL
  has no password in it and the token is single-use.
- **Audit — challenge answers scrubbed from the repo.** The v2.9
  README-challenge flag and the flag.txt demo flag were written in
  plaintext in `tests/test_v2_9.py` and in this changelog — any
  player could have grepped a public repo for the answer. Both
  scrubbed: the tests now assert the decoded flag's SHAPE
  (`BB{...}`) instead of its value, and this file no longer prints
  either flag. The flags live only with the author (chat + private
  memory). The rules.txt sample flag and the flag.txt base64 string
  stay — they are meant to be visible in the terminal.
- **Cleanup — unnecessary files removed.** `ByteBatallion Master
  Prompt.md` (the build prompt, not part of the running app) is out
  of the tree — a copy is kept in the workspace for reference.
  Nothing else was junk: `Dockerfile`/`docker-compose.yml` work and
  are documented, `scripts/smoke_test.py` is the load check,
  `UI-CHANGES.md` is the changelog. The zip contains no
  `.venv`/`.db`/`.git`/`__pycache__`/`.pytest_cache`.
- **GitHub files added.** `LICENSE` (MIT, Purnendu Rai, 2026) and
  a rewritten README status section (v0.1 features + audit notes).
  `DEPLOYMENT.md` now points at the real repo:
  https://github.com/purnendu-rai/ByteBatallion-CTF-Platform
  (create it at https://github.com/new — no repo exists yet).
- Tests: password-reset test extended (token-only redirect,
  single-use); v2.9 flag tests changed to shape-only assertions.
  Full suite: 132 passed.
