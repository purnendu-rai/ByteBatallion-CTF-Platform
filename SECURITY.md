# Security — ByteBatallion CTF Platform

## How to report a vulnerability

**Do not open a public issue with exploit details.** Instead:

1. Preferred: open a **GitHub Security Advisory** on this repo
   (Security tab → "Report a vulnerability"), or
2. Message the maintainer privately with the details.

Include: what you did, the exact request / input, what you expected vs.
what happened, and the impact as you see it. The maintainer will triage
it and fix confirmed issues before disclosing them publicly.

In scope: everything in this repo — authentication, the flag engine,
flag submission, the admin panel, teams, file uploads, and the scoreboard.
Out of scope: the hosting provider's infrastructure, and social
engineering of players or admins.

## Security model (in 10 lines)

1. **SQL injection — no:** every query is SQLAlchemy ORM with bound
   parameters. The one raw-SQL statement in the tree
   (`app/database.py`, the additive-column migration) is built only from
   a hardcoded internal dict — table/column/DDL never come from input.
2. **XSS — no:** Jinja2 autoescape is on and no template uses `| safe`.
   The one place user text reaches `innerHTML` (the scoreboard tooltip)
   escapes it first (`escapeHtml` in `scoreboard.js`) — this matters
   because team names are free text while player names are locked to
   `[A-Za-z0-9_]` at registration.
3. **CSRF — no:** double-submit cookie; `verify_csrf` is a dependency
   on every state-changing POST (auth, admin, challenges, submissions,
   settings, teams). No GET route mutates state.
4. **Sessions:** the cookie holds only a signed user id
   (`HttpOnly`, `SameSite=lax`, `Secure` in prod). Bans re-check the
   user row on every request, so they take effect immediately.
5. **Headers:** every response carries a strict CSP, `X-Frame-Options:
   DENY`, `nosniff`, a referrer policy and a permissions policy; HSTS
   only in prod (never on plain-http dev).
6. **Login brute force:** 5 failed logins per (IP, username) → 15-minute
   lockout (`LoginRateLimiter` in `app/security.py`), reset on success.
   Flag submissions are rate-limited per user too (1 per 5s, max 20/min).
7. **Flags:** only SHA-256 hashes are stored; per-user flags are derived
   per player, so sharing a flag never works and a leaked DB reveals no
   plaintext flags.
8. **Instance downloads:** 5-minute signed tokens bound to one user +
   one challenge; a shared link is useless to anyone else.
9. **Secrets:** never in the repo — `.env`, `*.db` and runtime data are
   git-ignored; the Docker image deletes `.env` and runs as a non-root
   user; config comes from environment variables only.
10. **Terminal:** the home-page shell is 100% client-side JavaScript
    with no network calls; its `bb` commands are text stubs and can
    never read the server, the database, or any flag (see the SECURITY
    block at the top of `terminal.js`).

## Notes / deliberate compromises

- **CSP allows `style-src-attr 'unsafe-inline'`:** templates and the
  scoreboard tooltip use inline `style="..."` attributes for colours.
  A style attribute cannot execute script in a modern browser, and
  blocking it would strip colours across the UI. Inline `<script>`,
  inline event handlers (`onchange="..."` etc.) and `<style>` elements
  are all forbidden by the policy — the last two were moved to a static
  file / a JSON data block in v2.5 to make that possible.
- **Google Fonts is the only external origin** allowed (font CSS + font
  files). Everything else is served from the app itself.
- **Rate limiters are in-memory** and reset on server restart; that is
  fine for a single-process deployment. A multi-replica deployment
  would need a shared store (e.g. Redis) for the limiters.
- **SQLite** is the default database: fine for a small event, but back
  up `data/bytebatallion.db` — it holds everything. See DEPLOYMENT.md.
