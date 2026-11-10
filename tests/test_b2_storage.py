"""v3.3 — Backblaze B2 file storage (S3-compatible API via boto3).

Acceptance:
- with B2_* unset, everything behaves exactly as before (local disk)
- with B2_* set, challenge files round-trip through the bucket:
  upload -> list -> download returns the same bytes
- the upload gates (extension blocklist, size cap) apply identically
  on the B2 backend, and a partial B2 config refuses loudly
- downloads stay login-gated and traversal-safe on both backends
"""

import asyncio
import io
import re
import shutil

import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from app.config import Settings
from app.database import SessionLocal
from app.flag_engine import DATA_DIR, sha256_hex
from app.main import app
from app.models import (
    Challenge, CheatLog, ConfigEntry, HintUnlock, Instance, Page,
    Solve, Submission, Team, TeamMembership, User,
)
from app.security import (
    hash_password, login_rate_limiter, submission_rate_limiter,
)
from app.storage import (
    B2ChallengeStorage, LocalChallengeStorage, clear_storage_override,
    get_storage, set_storage_override,
)

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
SMOKE_FLAG = "BB{b2_smoke_abc123}"
FILE_BODY = b"the flag: FLAG_PLACEHOLDER end"


# --- a fake S3 client (in-memory "bucket") -------------------------------------


class FakePaginator:
    def __init__(self, objects):
        self.objects = objects

    def paginate(self, Bucket, Prefix):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        yield {"Contents": [{"Key": k} for k in keys]}


class FakeS3Client:
    """Minimal in-memory stand-in for a boto3 S3 client."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def upload_fileobj(self, fp, bucket, key, ExtraArgs=None):
        self.objects[key] = fp.read()

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "not found"}},
                "GetObject",
            )
        return {
            "Body": io.BytesIO(self.objects[Key]),
            "ContentLength": len(self.objects[Key]),
            "ContentType": "text/plain",
        }

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return FakePaginator(self.objects)

    def delete_objects(self, Bucket, Delete):
        for obj in Delete["Objects"]:
            self.objects.pop(obj["Key"], None)
        return {"Deleted": Delete["Objects"]}


def b2_settings() -> Settings:
    return Settings(
        b2_bucket_name="bytebatallion-files",
        b2_key_id="test-key-id",
        b2_application_key="test-app-key",
        b2_endpoint="https://s3.us-west-000.backblazeb2.com",
    )


def b2_storage():
    """A B2 backend wired to the fake client (no network)."""
    fake = FakeS3Client()
    return B2ChallengeStorage(b2_settings(), client=fake), fake


@pytest.fixture(autouse=True)
def _isolate():
    login_rate_limiter.reset_all()
    submission_rate_limiter.reset_all()
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
    yield
    clear_storage_override()
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


def login(client: TestClient, username: str, password: str = "password123"):
    r = client.post(
        "/auth/login",
        data={"csrf_token": get_csrf(client, "/auth/login"),
              "username": username, "password": password},
        follow_redirects=False,
    )
    assert r.status_code == 303
    return r


def make_admin(username: str = "admin1"):
    db = SessionLocal()
    try:
        db.add(User(username=username, email=f"{username}@upsifs.in",
                    password_hash=hash_password("password123"), role="admin"))
        db.commit()
    finally:
        db.close()


def make_player(username: str):
    db = SessionLocal()
    try:
        db.add(User(username=username, email=f"{username}@upsifs.in",
                    password_hash=hash_password("password123"), role="player"))
        db.commit()
    finally:
        db.close()


def create_challenge_via_ui(client: TestClient, filename="note.txt",
                            body=FILE_BODY) -> int:
    token = get_csrf(client, "/admin/challenges/new")
    r = client.post(
        "/admin/challenges/new",
        data={"csrf_token": token, "title": "B2 Challenge",
              "category": "misc", "description": "b2 test",
              "points": "10", "hints": "", "flag": SMOKE_FLAG},
        files=[("files", (filename, io.BytesIO(body), "text/plain"))],
        follow_redirects=False)
    assert r.status_code == 303, f"challenge create failed: {r.status_code}"
    return int(re.search(r"/admin/challenges/(\d+)/edit",
                         r.headers["location"]).group(1))


# --- backend selection ----------------------------------------------------------


def test_local_fallback_when_b2_unset():
    assert isinstance(get_storage(settings=Settings()), LocalChallengeStorage)
    assert get_storage(settings=Settings()).kind == "local"


def test_partial_b2_config_refuses_loudly():
    with pytest.raises(RuntimeError, match="B2_BUCKET_NAME|missing"):
        B2ChallengeStorage(Settings(b2_bucket_name="bytebatallion-files"))
    with pytest.raises(RuntimeError, match="B2_ENDPOINT"):
        B2ChallengeStorage(Settings(
            b2_bucket_name="bytebatallion-files", b2_key_id="k",
            b2_application_key="s"))
    # A complete config builds fine (client injected, no network).
    storage, _ = b2_storage()
    assert storage.kind == "b2"
    assert storage.bucket == "bytebatallion-files"


# --- B2 round-trip through the storage layer ------------------------------------


def test_b2_save_list_download_delete_direct():
    storage, fake = b2_storage()
    files = [UploadFile(file=io.BytesIO(b"hello b2"), filename="a.txt"),
             UploadFile(file=io.BytesIO(FILE_BODY), filename="note.txt")]
    saved = asyncio.run(storage.save_files(7, files))
    assert saved == ["a.txt", "note.txt"]
    assert fake.objects["challenges/7/originals/a.txt"] == b"hello b2"
    assert storage.list_files(7) == ["a.txt", "note.txt"]
    assert storage.list_files(999) == []
    storage.delete_files(7)
    assert storage.list_files(7) == []
    assert fake.objects == {}


def test_b2_upload_gates_match_local(monkeypatch):
    from app import flag_engine

    storage, fake = b2_storage()
    # Blocked extension -> 400, nothing stored.
    with pytest.raises(HTTPException) as exc:
        asyncio.run(storage.save_files(
            8, [UploadFile(file=io.BytesIO(b"MZ"), filename="evil.exe")]))
    assert exc.value.status_code == 400
    assert fake.objects == {}
    # Over the size cap -> 413, nothing stored.
    monkeypatch.setattr(flag_engine, "MAX_FILE_BYTES", 128)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(storage.save_files(
            8, [UploadFile(file=io.BytesIO(b"x" * 2048), filename="big.bin")]))
    assert exc.value.status_code == 413
    assert fake.objects == {}


def test_b2_download_missing_and_traversal_are_404():
    storage, _ = b2_storage()
    with pytest.raises(HTTPException) as exc:
        storage.download_response(9, "ghost.txt")
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        storage.download_response(9, "../secret.txt")
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        storage.download_response(9, "sub/secret.txt")
    assert exc.value.status_code == 404


# --- B2 end-to-end through the HTTP routes ---------------------------------------


def test_b2_upload_and_download_via_http():
    storage, fake = b2_storage()
    set_storage_override(storage)
    make_admin()
    with TestClient(app) as admin_client:
        login(admin_client, "admin1")
        cid = create_challenge_via_ui(admin_client)
        # The file is in the (fake) bucket, not on local disk.
        assert fake.objects[f"challenges/{cid}/originals/note.txt"] == FILE_BODY
        assert not (DATA_DIR / "challenges" / str(cid)).exists()
        # Admin edit page lists it.
        r = admin_client.get(f"/admin/challenges/{cid}/edit")
        assert "note.txt" in r.text
        # Publish, then a player downloads the same bytes.
        admin_client.post(
            f"/admin/challenges/{cid}/publish",
            data={"csrf_token": get_csrf(
                admin_client, f"/admin/challenges/{cid}/edit")},
            follow_redirects=False)
    make_player("player1")
    with TestClient(app) as player_client:
        login(player_client, "player1")
        dl = player_client.get(f"/challenges/{cid}/download/note.txt")
        assert dl.status_code == 200
        assert dl.content == FILE_BODY
        # Missing file -> 404 for a logged-in player.
        assert player_client.get(
            f"/challenges/{cid}/download/ghost.txt").status_code == 404
    # Logged out -> sent to login, never the file.
    with TestClient(app) as anon:
        r = anon.get(f"/challenges/{cid}/download/note.txt",
                     follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"].startswith("/auth/login?next=")


def test_b2_blocked_upload_via_http():
    storage, fake = b2_storage()
    set_storage_override(storage)
    make_admin()
    with TestClient(app) as admin_client:
        login(admin_client, "admin1")
        token = get_csrf(admin_client, "/admin/challenges/new")
        r = admin_client.post(
            "/admin/challenges/new",
            data={"csrf_token": token, "title": "Blocked up",
                  "category": "misc", "description": "x", "points": "10",
                  "hints": "", "flag": SMOKE_FLAG},
            files=[("files", ("evil.exe", io.BytesIO(b"MZ" + b"\x00" * 32),
                              "application/octet-stream"))],
            follow_redirects=True)
        assert r.status_code == 400
        assert fake.objects == {}


def test_local_backend_still_works_after_refactor():
    """The refactor must not change local-disk behaviour (the default)."""
    assert isinstance(get_storage(settings=Settings()), LocalChallengeStorage)
    make_admin()
    with TestClient(app) as admin_client:
        login(admin_client, "admin1")
        cid = create_challenge_via_ui(admin_client)
        assert (DATA_DIR / "challenges" / str(cid)
                / "originals" / "note.txt").read_bytes() == FILE_BODY
        dl = admin_client.get(f"/challenges/{cid}/download/note.txt")
        assert dl.status_code == 200
        assert dl.content == FILE_BODY
