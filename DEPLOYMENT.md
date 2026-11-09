# Deployment — publishing ByteBatallion online

The app is a plain FastAPI app (see `Dockerfile`, `docker-compose.yml`,
`run.py`). It needs exactly one thing to persist: the `data/` directory
(SQLite DB + uploaded challenge files + generated instances). **If the
container is ephemeral, `data/` must live on a persistent volume, or
every redeploy wipes the event.**

## Required environment variables

| Variable | Value |
|---|---|
| `SECRET_KEY` | A long random string — **never the dev default**. Generate one: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | `sqlite:///./data/bytebatallion.db` (default; change only to move to PostgreSQL) |
| `HOST` | `0.0.0.0` inside a container (so the host/proxy can reach it) |
| `PORT` | `8000` (or the platform's `$PORT` if it injects one) |
| `ENVIRONMENT` | `prod` — enables the `Secure` cookie flag + HSTS |
| `DEBUG` | `false` |

## Option A — Render (easiest)

1. Push this repo to GitHub (see "Publishing the repo" below).
2. Render → New → Web Service → pick the repo, Runtime: **Docker**.
3. Set the env vars above (`SECRET_KEY`, `ENVIRONMENT=prod`).
4. Add a **Persistent Disk** mounted at `/app/data` (paid plans) —
   without it, the SQLite DB resets on every deploy. Set the disk size
   to at least 1 GB.
5. Deploy. Render terminates TLS for you; the app only needs to listen
   on `$PORT` (Render injects `PORT` — set `PORT` from it, or leave the
   default 8000 and let Render map it).

## Option B — Railway

1. New Project → Deploy from GitHub repo (Dockerfile is detected).
2. Add the env vars above.
3. Add a **Volume** mounted at `/app/data` (this is what survives
   redeploys). Railway terminates TLS for you.

## Option C — Fly.io

1. `fly launch` (accept the Dockerfile), then
   `fly volumes create bb_data --size 1` and mount it at `/app/data`
   in `fly.toml` (`[[mounts]] source="bb_data" destination="/app/data"`).
2. `fly secrets set SECRET_KEY=... ENVIRONMENT=prod` (never put the
   secret in `fly.toml`).
3. `fly deploy`. Fly terminates TLS for you.

## Option D — VPS + Docker + Caddy (full control)

1. On the VPS: install Docker + Caddy.
2. Clone the repo, `cp .env.example .env`, edit `.env` (real
   `SECRET_KEY`, `ENVIRONMENT=prod`, `HOST=0.0.0.0`).
3. `docker compose up -d --build` (the compose file mounts `./data`
   into the container, so the DB survives restarts).
4. Caddyfile (Caddy gets a free HTTPS certificate automatically):
   ```
   ctf.example.com {
       reverse_proxy localhost:8000
   }
   ```
   Point your domain's DNS at the VPS, `systemctl reload caddy`.
5. Back up `./data` regularly
   (`tar -czf bb-backup-$(date +%F).tar.gz data/`).

## Prod checklist (do all of these before announcing the URL)

- [ ] `SECRET_KEY` is set, long and random (the app refuses to boot in
      prod with the dev default — `run.py` validates this).
- [ ] `ENVIRONMENT=prod` and `DEBUG=false`.
- [ ] HTTPS is on (platform TLS, or Caddy/Nginx in front). HSTS is
      sent only when `ENVIRONMENT=prod`.
- [ ] `data/` is on a persistent volume / backed up.
- [ ] First admin created:
      `docker compose exec app python -m app.create_admin admin1 you@example.com`
      (or `python -m app.create_admin ...` on a plain install).
- [ ] Registration open/closed as intended (Admin → Config), and the
      scoreboard visibility + CTF phase set.
- [ ] A test player account can register, solve, and submit a flag.

## Updating a live deployment

1. `git pull` (or redeploy from GitHub on Render/Railway/Fly).
2. Rebuild + restart (`docker compose up -d --build`, or the
   platform's redeploy). The DB file in `data/` is untouched by a
   rebuild; additive migrations run automatically on startup.
3. Smoke-test: home page loads, login works, one flag submits.

## Publishing the repo (GitHub)

This repo is ready to push — `.gitignore` excludes `.env*` (except
`.env.example`), `*.db`, `.venv*`, `__pycache__`, test DBs and runtime
data. Verify before the first push with `git status --porcelain` and
confirm no `.env`, `*.db` or `.venv` path is listed.

```bash
cd ByteBatallion
git init
git add .
git status --porcelain        # check: no .env / *.db / .venv lines
git commit -m "ByteBatallion CTF Platform — initial public release"
git branch -M main
git remote add origin https://github.com/purnendu-rai/ByteBatallion-CTF-Platform.git
git push -u origin main
```

The repo will live at https://github.com/purnendu-rai/ByteBatallion-CTF-Platform
(create it first at https://github.com/new with the name
`ByteBatallion-CTF-Platform`, or let Muse create it for you).

If you ever committed a secret by accident, rotate it immediately —
rewriting history does not un-leak a secret that was already pushed.
