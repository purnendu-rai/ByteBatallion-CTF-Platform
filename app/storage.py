"""Challenge file storage — local disk or Backblaze B2 (v3.3).

Before v3.3 every challenge file lived under
``data/challenges/<id>/originals/`` on the host's disk. On Render's free
tier that disk is wiped on every restart, so uploaded files vanished
even though their records (now in Turso) survived. This module adds a
second backend: Backblaze B2 through its S3-compatible API (boto3),
free tier 10 GB, no card.

Backend selection — ``get_storage()``:
- ``B2_BUCKET_NAME`` set (with ``B2_KEY_ID``, ``B2_APPLICATION_KEY``,
  ``B2_ENDPOINT``) -> B2. A partial B2 config refuses to build a
  client with a clear error instead of silently falling back.
- otherwise -> local disk (the pre-v3.3 behaviour, still the default
  for local development on the user's laptop).

Both backends share one set of rules, enforced here so the routes
never re-implement them:
1. sanitized filename (no paths / weird characters)
2. extension blocklist (executables & active scripts -> 400)
3. size cap while streaming (default 50 MB -> 413)

B2 key layout mirrors the disk layout:
``challenges/<id>/originals/<sanitized-filename>``.

Security notes:
- B2 credentials are NEVER logged and NEVER appear in error messages
  or responses; a B2 failure surfaces as a generic 502.
- Downloads are always served by the app (login-gated route), never
  by a public B2 URL — the bucket stays private.
- ``delete_files()`` exists for cleanup but is NOT wired to the
  challenge soft-delete: like the local disk before it, a soft-deleted
  challenge keeps its files (parity with the pre-v3.3 behaviour).
"""

import mimetypes
import shutil
import tempfile
from pathlib import Path

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from starlette.concurrency import iterate_in_threadpool

from app import flag_engine
from app.config import Settings, get_settings

# Spool threshold: uploads stream into memory up to this size, then
# spill to a temp file — a 50 MB upload never holds 50 MB in RAM.
SPOOL_MAX_MEMORY = 8 * 1024 * 1024
CHUNK = 1024 * 1024


def _b2_key(challenge_id: int, filename: str) -> str:
    return f"challenges/{challenge_id}/originals/{filename}"


def _b2_prefix(challenge_id: int) -> str:
    return f"challenges/{challenge_id}/originals/"


async def _spool_upload(upload: UploadFile, safe: str) -> tempfile.SpooledTemporaryFile:
    """Stream one upload into a spool file, enforcing the size cap.

    Raises 413 (and leaves nothing behind) when the file is over the
    cap. The size cap is read from ``flag_engine`` at call time so
    tests can monkeypatch it.
    """
    spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_MAX_MEMORY, mode="w+b")
    size = 0
    try:
        while chunk := await upload.read(CHUNK):
            size += len(chunk)
            if size > flag_engine.MAX_FILE_BYTES:
                raise HTTPException(status_code=413, detail=f"File too large: {safe}")
            spool.write(chunk)
    except BaseException:
        spool.close()
        raise
    spool.seek(0)
    return spool


class LocalChallengeStorage:
    """The original backend: files under data/challenges/<id>/originals/."""

    kind = "local"

    def list_files(self, challenge_id: int) -> list[str]:
        d = flag_engine.originals_dir(challenge_id)
        if not d.is_dir():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_file())

    async def save_files(
        self, challenge_id: int, files: list[UploadFile] | None
    ) -> list[str]:
        if not files:
            return []
        dest_dir = flag_engine.originals_dir(challenge_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        saved: list[str] = []
        for upload in files:
            if not upload or not upload.filename:
                continue
            safe = flag_engine.sanitize_filename(upload.filename)
            if safe is None:
                continue
            if flag_engine.extension_blocked(safe):
                raise HTTPException(
                    status_code=400,
                    detail=f"Blocked file type — not allowed on this platform: {safe}",
                )
            spool = await _spool_upload(upload, safe)
            dest = dest_dir / safe
            try:
                with dest.open("wb") as out:
                    shutil.copyfileobj(spool, out, length=CHUNK)
            except BaseException:
                dest.unlink(missing_ok=True)
                raise
            finally:
                spool.close()
            if safe not in saved:
                saved.append(safe)
        return saved

    def download_response(self, challenge_id: int, filename: str) -> Response:
        base = flag_engine.originals_dir(challenge_id).resolve()
        target = (base / filename).resolve()
        if not target.is_relative_to(base) or not target.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(target, filename=target.name)

    def delete_files(self, challenge_id: int) -> None:
        d = flag_engine.originals_dir(challenge_id)
        if d.is_dir():
            shutil.rmtree(d)

    def delete_file(self, challenge_id: int, filename: str) -> None:
        """Delete ONE uploaded file (admin action from the edit form).
        Missing files are a no-op — the delete is idempotent."""
        base = flag_engine.originals_dir(challenge_id).resolve()
        target = (base / filename).resolve()
        if not target.is_relative_to(base):
            return
        target.unlink(missing_ok=True)


class B2ChallengeStorage:
    """Backblaze B2 backend (S3-compatible API via boto3)."""

    kind = "b2"

    def __init__(self, settings: Settings, client=None):
        missing = [
            name
            for name, value in (
                ("B2_BUCKET_NAME", settings.b2_bucket_name),
                ("B2_KEY_ID", settings.b2_key_id),
                ("B2_APPLICATION_KEY", settings.b2_application_key),
                ("B2_ENDPOINT", settings.b2_endpoint),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "B2 file storage is partially configured — missing: "
                + ", ".join(missing)
                + ". Set all four B2_* variables, or unset B2_BUCKET_NAME "
                "to keep local disk storage."
            )
        self.bucket = settings.b2_bucket_name
        if client is not None:
            self.client = client
        else:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - boto3 is pinned
                raise RuntimeError(
                    "boto3 is required for B2 file storage — "
                    "install it with: pip install -r requirements.txt"
                ) from exc
            self.client = boto3.client(
                "s3",
                endpoint_url=settings.b2_endpoint,
                aws_access_key_id=settings.b2_key_id,
                aws_secret_access_key=settings.b2_application_key,
            )

    # -- helpers ---------------------------------------------------------------

    def _client_error_404(self, exc) -> bool:
        code = exc.response.get("Error", {}).get("Code", "")
        return code in ("404", "NoSuchKey", "NotFound")

    # -- backend API ------------------------------------------------------------

    def list_files(self, challenge_id: int) -> list[str]:
        from botocore.exceptions import ClientError

        prefix = _b2_prefix(challenge_id)
        names: list[str] = []
        try:
            paginator = self.client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    names.append(Path(obj["Key"]).name)
        except ClientError as exc:
            raise HTTPException(
                status_code=502, detail="Could not list challenge files — try again."
            ) from exc
        return sorted(names)

    async def save_files(
        self, challenge_id: int, files: list[UploadFile] | None
    ) -> list[str]:
        from botocore.exceptions import ClientError

        if not files:
            return []
        saved: list[str] = []
        for upload in files:
            if not upload or not upload.filename:
                continue
            safe = flag_engine.sanitize_filename(upload.filename)
            if safe is None:
                continue
            if flag_engine.extension_blocked(safe):
                raise HTTPException(
                    status_code=400,
                    detail=f"Blocked file type — not allowed on this platform: {safe}",
                )
            spool = await _spool_upload(upload, safe)
            try:
                content_type = (
                    mimetypes.guess_type(safe)[0] or "application/octet-stream"
                )
                self.client.upload_fileobj(
                    spool,
                    self.bucket,
                    _b2_key(challenge_id, safe),
                    ExtraArgs={"ContentType": content_type},
                )
            except ClientError as exc:
                raise HTTPException(
                    status_code=502, detail="Could not store the file — try again."
                ) from exc
            finally:
                spool.close()
            if safe not in saved:
                saved.append(safe)
        return saved

    def download_response(self, challenge_id: int, filename: str) -> Response:
        from botocore.exceptions import ClientError

        safe = flag_engine.sanitize_filename(filename)
        if safe is None or safe != filename:
            raise HTTPException(status_code=404)
        try:
            obj = self.client.get_object(
                Bucket=self.bucket, Key=_b2_key(challenge_id, safe)
            )
        except ClientError as exc:
            if self._client_error_404(exc):
                raise HTTPException(status_code=404) from exc
            raise HTTPException(
                status_code=502, detail="Could not fetch the file — try again."
            ) from exc
        body = obj["Body"]
        content_type = obj.get("ContentType") or (
            mimetypes.guess_type(safe)[0] or "application/octet-stream"
        )

        def _chunks():
            while True:
                data = body.read(CHUNK)
                if not data:
                    break
                yield data

        headers = {
            "Content-Disposition": f'attachment; filename="{safe}"',
        }
        if obj.get("ContentLength") is not None:
            headers["Content-Length"] = str(obj["ContentLength"])
        # The B2 body is a blocking stream — iterate it in a threadpool
        # so a big download never stalls the event loop.
        return StreamingResponse(
            iterate_in_threadpool(_chunks()),
            media_type=content_type,
            headers=headers,
        )

    def delete_files(self, challenge_id: int) -> None:
        from botocore.exceptions import ClientError

        prefix = _b2_prefix(challenge_id)
        try:
            paginator = self.client.get_paginator("list_objects_v2")
            keys = [
                {"Key": obj["Key"]}
                for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix)
                for obj in page.get("Contents", [])
            ]
            for i in range(0, len(keys), 1000):
                self.client.delete_objects(
                    Bucket=self.bucket, Delete={"Objects": keys[i : i + 1000]}
                )
        except ClientError as exc:
            raise HTTPException(
                status_code=502, detail="Could not delete challenge files — try again."
            ) from exc

    def delete_file(self, challenge_id: int, filename: str) -> None:
        """Delete ONE uploaded file (admin action from the edit form).
        Missing files are a no-op — the delete is idempotent."""
        from botocore.exceptions import ClientError

        safe = flag_engine.sanitize_filename(filename)
        if safe is None or safe != filename:
            return
        try:
            self.client.delete_object(
                Bucket=self.bucket, Key=_b2_key(challenge_id, safe)
            )
        except ClientError as exc:
            raise HTTPException(
                status_code=502, detail="Could not delete the file — try again."
            ) from exc


# --- backend selection ----------------------------------------------------------

# Test override: tests point the app at a fake B2 client through this.
# Routes always call get_storage() at request time, so the override
# takes effect without restarting the app.
_override: LocalChallengeStorage | B2ChallengeStorage | None = None


def set_storage_override(storage) -> None:
    global _override
    _override = storage


def clear_storage_override() -> None:
    global _override
    _override = None


def get_storage(settings: Settings | None = None, client=None):
    """Return the active challenge-file backend for these settings."""
    if _override is not None:
        return _override
    s = settings or get_settings()
    if s.b2_bucket_name:
        return B2ChallengeStorage(s, client=client)
    return LocalChallengeStorage()


# Re-exported for callers that already import from flag_engine.
__all__ = [
    "B2ChallengeStorage",
    "LocalChallengeStorage",
    "clear_storage_override",
    "get_storage",
    "set_storage_override",
]
