#!/usr/bin/env python
"""Load smoke test against a RUNNING BYTE_BATALLION_CTF_PLATFORM server.

    python scripts/smoke_test.py [BASE_URL]   # default http://127.0.0.1:8000

Needs an admin account admin1 / password123 on that server (create it
with `python -m app.create_admin admin1 admin@example.com`).

Simple single-flag model:
- the admin creates ONE challenge with ONE flag;
- 20 fake users register (one concurrent wave);
- each downloads the challenge file;
- each submits the CORRECT flag -> must score;
- each submits a WRONG flag -> must be rejected;
- the server must stay up and responsive throughout.

Exit code 0 = everything passed.
"""

import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
USERS = 20
SMOKE_FLAG = "BB{smoke_abc123xyz}"
CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
FILE_BODY = b"the challenge notes: no flag inside"


def csrf(client: httpx.Client, path: str) -> str:
    m = CSRF_RE.search(client.get(path).text)
    assert m, f"no csrf token found on {path}"
    return m.group(1)


def register(client: httpx.Client, username: str) -> None:
    r = client.post("/auth/register", data={
        "csrf_token": csrf(client, "/auth/login"), "username": username,
        "email": f"{username}@smoke.in", "password": "password123"},
        follow_redirects=False)
    assert r.status_code == 303, f"register {username}: {r.status_code}"


def submit(client: httpx.Client, cid: int, flag: str) -> str:
    """Returns the result parameter the platform redirected with."""
    r = client.post(f"/submit/{cid}",
                    data={"csrf_token": csrf(client, f"/challenges/{cid}"),
                          "flag": flag},
                    follow_redirects=False)
    assert r.status_code == 303, f"submit: {r.status_code}"
    m = re.search(r"result=([a-z]+)", r.headers["location"])
    return m.group(1) if m else "correct"


def main() -> int:
    print(f"smoke test against {BASE} — {USERS} users")

    # --- admin seeds ONE published challenge with ONE flag ---
    cid: int
    with httpx.Client(base_url=BASE) as admin:
        r = admin.post("/auth/login", data={
            "csrf_token": csrf(admin, "/auth/login"),
            "username": "admin1", "password": "password123"},
            follow_redirects=False)
        assert r.status_code == 303, "need admin1/password123 to exist"
        r = admin.post("/admin/challenges/new", data={
            "csrf_token": csrf(admin, "/admin/challenges/new"),
            "title": "Smoke Challenge", "category": "misc",
            "description": "load smoke test challenge", "points": "10",
            "hints": "", "flag": SMOKE_FLAG},
            files=[("files", ("note.txt", __import__("io").BytesIO(FILE_BODY), "text/plain"))],
            follow_redirects=False)
        assert r.status_code == 303, "challenge seed failed"
        cid = int(re.search(r"/admin/challenges/(\d+)/edit", r.headers["location"]).group(1))
        admin.post(f"/admin/challenges/{cid}/publish",
                   data={"csrf_token": csrf(admin, f"/admin/challenges/{cid}/edit")},
                   follow_redirects=False)
    print(f"seeded challenge {cid}")

    # --- one client per user (each keeps own cookies) ---
    def setup(i: int):
        c = httpx.Client(base_url=BASE)
        name = f"smokeuser{i:02d}"
        register(c, name)
        # the challenge file downloads for a signed-in player
        dl = c.get(f"/challenges/{cid}/download/note.txt")
        assert dl.status_code == 200, f"{name}: file download failed"
        return name, c

    with ThreadPoolExecutor(max_workers=USERS) as pool:
        users = list(pool.map(setup, range(USERS)))
    clients = [c for _, c in users]

    # --- wave: everyone submits the CORRECT flag -> must score ---------
    def solve(item):
        name, c = item
        return submit(c, cid, SMOKE_FLAG)

    with ThreadPoolExecutor(max_workers=USERS) as pool:
        own_results = list(pool.map(solve, users))
    own_correct = sum(1 for r in own_results if r in ("correct", "already"))

    # --- wave: everyone submits a WRONG flag -> must be rejected -------
    def attack(item):
        name, c = item
        return submit(c, cid, "BB{definitely_wrong}")

    with ThreadPoolExecutor(max_workers=USERS) as pool:
        attack_results = list(pool.map(attack, users))
    rejected = sum(1 for r in attack_results if r in ("incorrect", "ratelimited"))

    for c in clients:
        c.close()

    print(f"\ncorrect-flag submissions accepted: {own_correct}/{USERS} (must be {USERS})")
    print(f"wrong-flag submissions rejected:   {rejected}/{USERS} (must be {USERS})")

    # --- admin cleans up the seeded challenge so re-runs stay clean ----
    with httpx.Client(base_url=BASE) as admin:
        admin.post("/auth/login", data={
            "csrf_token": csrf(admin, "/auth/login"),
            "username": "admin1", "password": "password123"},
            follow_redirects=False)
        admin.post(f"/admin/challenges/{cid}/delete",
                   data={"csrf_token": csrf(admin, f"/admin/challenges/{cid}/edit")},
                   follow_redirects=False)

    if own_correct != USERS:
        print("FAIL: not every correct flag scored — server error under load?")
        return 1
    if rejected != USERS:
        print("FAIL: a wrong flag was ACCEPTED")
        return 1
    print("PASS: server up; correct flags score; wrong flags rejected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
