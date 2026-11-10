"""THE flag engine — the heart of ByteBatallion (Phase 3).

Every player gets their OWN flag for every challenge: the author writes
ONE template per challenge; the platform keeps the core exactly as
written and generates a fresh tail for each (user, challenge) using the
`secrets` module — the predictable `random` module is NEVER used here.

RULES (brief, Sections 1 and 9):
- the DB stores ONLY the SHA-256 hash of each player's flag — no
  plaintext flag in the database, logs, error messages, or API responses
- the static core alone never validates
- another user's flag never validates (the Phase 4 checker is scoped to
  (user, challenge) and only ever compares the ACTIVE instance hash)
- resetting an instance invalidates the old flag

This module also owns the on-disk layout for challenge/instance files:
  data/challenges/<id>/originals/    author uploads, never web-served
  data/instances/<user>/<chal>/      per-user copies + instance.zip
"""

import hashlib
import os
import re
import secrets
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

# --- on-disk paths ------------------------------------------------------------

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

# The exact byte string the author puts in their files; it is replaced
# with the player's full flag when their personal copy is generated.
FLAG_PLACEHOLDER = b"FLAG_PLACEHOLDER"


def originals_dir(challenge_id: int) -> Path:
    return DATA_DIR / "challenges" / str(challenge_id) / "originals"


def instances_dir(user_id: int, challenge_id: int) -> Path:
    return DATA_DIR / "instances" / str(user_id) / str(challenge_id)


# --- template validation (used by the admin form since Phase 2) ----------------

# {RAND:12} -> 12 alphanumeric chars; {RANDSYM:12} -> author allows symbols
PLACEHOLDER_RE = re.compile(r"\{RAND(SYM)?:(\d+)\}")
MIN_TAIL_LEN = 10   # brief: a tail is never shorter than 10 characters
MAX_TAIL_LEN = 64
DEFAULT_TAIL_LEN = 12

# Valid endings for an append-mode template (`BB{core_`).
TRAILING_SEPARATORS = set("-_ {!")

_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]")


def validate_flag_template(template: str) -> tuple[bool, str]:
    """Check the SHAPE of an author-written flag template.

    Valid forms (brief, Section 9.1):
      - placeholder mode: BB{web_sqli_{RAND:12}}
      - append mode:      BB{web_sqli_    (platform appends tail + `}`)

    Returns (ok, error_message).
    """
    template = template.strip()
    if not template:
        return False, "Flag template is required."
    if len(template) > 255:
        return False, "Flag template is too long (max 255 characters)."
    if "{" not in template:
        return False, "Template must look like PREFIX{core_ (e.g. BB{web_sqli_)."

    matches = list(PLACEHOLDER_RE.finditer(template))
    if "{RAND" in template and not matches:
        return False, "Placeholder must be written as {RAND:12} or {RANDSYM:12} (a number)."
    if len(matches) > 1:
        return False, "Use exactly ONE {RAND:n} placeholder per template."

    for m in matches:
        n = int(m.group(2))
        if n < MIN_TAIL_LEN or n > MAX_TAIL_LEN:
            return False, (
                f"Tail length must be between {MIN_TAIL_LEN} and {MAX_TAIL_LEN} (got {n})."
            )
    if matches:
        if template[-1] != "}":
            # The closing brace of the FLAG must be in the template itself —
            # the platform only swaps {RAND:n} for the tail.
            return False, "Placeholder templates must end with } (e.g. BB{core_{RAND:12}})."
        return True, ""

    if template[-1] in TRAILING_SEPARATORS:
        return True, ""

    return False, (
        "Template must contain a {RAND:12} placeholder or end with a trailing "
        "separator (e.g. BB{web_sqli_)."
    )


# --- tail + flag generation -----------------------------------------------------

TAIL_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
# The author may allow symbols in the tail (brief 9.2); braces/quotes are
# excluded so the tail can never break templates or file formats.
TAIL_SYMBOLS = "!#$%&()*+-=?@^_~"


@dataclass
class TemplateSpec:
    mode: str        # "placeholder" | "append"
    tail_len: int
    symbols: bool


def parse_template(template: str) -> TemplateSpec:
    """Read mode/length/symbols out of an already-validated template."""
    m = PLACEHOLDER_RE.search(template)
    if m:
        return TemplateSpec(mode="placeholder", tail_len=int(m.group(2)),
                            symbols=m.group(1) == "SYM")
    return TemplateSpec(mode="append", tail_len=DEFAULT_TAIL_LEN, symbols=False)


def generate_tail(length: int, symbols: bool = False) -> str:
    """Cryptographically secure tail — `secrets` only, never `random`."""
    alphabet = TAIL_ALPHABET + (TAIL_SYMBOLS if symbols else "")
    return "".join(secrets.choice(alphabet) for _ in range(length))


def build_flag(template: str, spec: TemplateSpec, tail: str) -> str:
    """Glue core + tail together. Plain str replace (no regex), so any
    tail characters — including symbols — are inserted literally."""
    if spec.mode == "placeholder":
        m = PLACEHOLDER_RE.search(template)
        return template.replace(m.group(0), tail)
    # append mode: the template ends with a separator; glue tail + `}`
    return f"{template}{tail}}}"


def new_flag_for(template: str) -> str:
    """Generate ONE fresh flag from a validated template."""
    ok, err = validate_flag_template(template)
    if not ok:
        raise ValueError(err)
    spec = parse_template(template)
    return build_flag(template, spec, generate_tail(spec.tail_len, spec.symbols))


def sha256_hex(flag: str) -> str:
    """The ONLY representation of a flag that ever touches the database."""
    return hashlib.sha256(flag.encode("utf-8")).hexdigest()


# --- simple single-flag mode (admin sets ONE flag per challenge) --------------
#
# The platform runs in a simplified model: when the admin creates a
# challenge they type ONE concrete flag; users must submit exactly that
# flag. The plaintext never reaches the DB — only its SHA-256 hash — and
# is never logged or shown after saving. This replaces the per-user
# instance/flag-injection engine for now.

# A challenge flag must look like PREFIX{something} (e.g. BB{...}).
CHALLENGE_FLAG_RE = re.compile(r"^[A-Za-z0-9_]{2,32}\{[^{}]{1,240}\}$")


def validate_challenge_flag(flag: str) -> tuple[bool, str]:
    """Check the shape of a single challenge flag: PREFIX{body}."""
    flag = (flag or "").strip()
    if not flag:
        return False, "Flag is required."
    if len(flag) > 255:
        return False, "Flag is too long (max 255 characters)."
    if not CHALLENGE_FLAG_RE.match(flag):
        return False, (
            "Flag must look like PREFIX{...} — e.g. BB{y0u_f0und_1t}."
        )
    return True, ""


def admin_sample_flag(template: str) -> str | None:
    """One test flag for the admin preview (brief 9.4). Rendered once on
    the admin edit page — never stored, never logged."""
    try:
        return new_flag_for(template)
    except ValueError:
        return None


# --- bulk-import conversion (brief 9.1) ------------------------------------------

_CORE_END = set("-_ {")   # a sliced/fallback core must end at one of these


def template_from_full_flag(flag: str, tail_len: int = DEFAULT_TAIL_LEN) -> str | None:
    """Convert an existing full flag (e.g. from an old CTF) into a template.

    Primary: keep everything except the last `tail_len` characters as the
    core and append a {RAND:n} placeholder — only when the slice is long
    enough AND structurally sound (core still ends at a separator).
    Fallback: append mode — core up to the last separator.
    Returns None when neither works (author writes it by hand).

    The converted template is ALWAYS shown to the author for review
    before publishing (brief 9.1).
    """
    flag = flag.strip()
    if len(flag) < 2 or not flag.endswith("}") or "{" not in flag:
        return None
    inner = flag[:-1]  # drop the closing brace

    core = inner[:-tail_len] if len(inner) > tail_len else ""
    if len(core) >= 2 and "{" in core and core[-1] in _CORE_END:
        return f"{core}{{RAND:{tail_len}}}}}"

    idx = max(inner.rfind(c) for c in "-_{")
    if idx > 0:
        return inner[: idx + 1]
    return None


# --- per-user instance files: injection + zip (brief 9.3) -------------------------

def generate_instance_files(challenge_id: int, user_id: int, flag: str) -> tuple[Path, int]:
    """Build the player's personal copy of the challenge files.

    Copies the originals to data/instances/<user>/<chal>/files/, replaces
    the exact byte string FLAG_PLACEHOLDER with the player's full flag
    (byte-level, so binary files work too), then re-zips everything into
    instance.zip. Returns (instance_dir, number_of_replacements).

    The plaintext flag exists ONLY inside the player's own instance files
    (and in memory during generation) — never in the database.
    """
    src = originals_dir(challenge_id)
    inst = instances_dir(user_id, challenge_id)
    if inst.exists():
        shutil.rmtree(inst)
    files_root = inst / "files"
    files_root.mkdir(parents=True)

    replacements = 0
    flag_bytes = flag.encode("utf-8")
    if src.is_dir():
        for path in sorted(src.rglob("*")):
            if not path.is_file():
                continue
            dest = files_root / path.relative_to(src)
            dest.parent.mkdir(parents=True, exist_ok=True)
            data = path.read_bytes()
            if FLAG_PLACEHOLDER in data:
                data = data.replace(FLAG_PLACEHOLDER, flag_bytes)
                replacements += data.count(flag_bytes)
            dest.write_bytes(data)

    zip_path = inst / "instance.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(files_root.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(files_root).as_posix())
    return inst, replacements


# --- author file storage helpers (used by the admin CRUD) --------------------------

def list_original_files(challenge_id: int) -> list[str]:
    """Filenames of the challenge's originals, via the active backend.

    Local disk by default; Backblaze B2 when B2_* is configured
    (app/storage.py). The admin form and the challenge detail page
    both read through here.
    """
    from app.storage import get_storage

    return get_storage().list_files(challenge_id)


def sanitize_filename(filename: str) -> str | None:
    """Flatten a filename and strip anything unsafe (no paths, no spaces)."""
    name = Path(filename).name
    name = _FILENAME_RE.sub("_", name).strip("._")
    return name[:150] or None


# --- upload validation (Phase 7) -------------------------------------------------
#
# "File-upload validation (size + type blocklist)": every challenge upload is
# size-capped and its extension is matched against a blocklist. The list only
# blocks things that DO NOT belong in a v1 file challenge (executable
# installers / active scripts); zips, pcaps, docs, images, binaries etc. pass
# so pwn-style artifacts can still be distributed later.
# Both limits come from environment variables so a deployment can tune them
# without touching code.

# Executable / active-script extensions — the classic drive-by download
# list. Tunable via UPLOAD_BLOCKED_EXT (comma-separated, lowercase).
BLOCKED_EXTENSIONS = tuple(
    ext.strip().lower().lstrip(".")
    for ext in os.environ.get(
        "UPLOAD_BLOCKED_EXT",
        "exe,msi,com,pif,scr,bat,cmd,ps1,vbs,vbe,wsf,hta,cpl,lnk,jar,dll,sys",
    ).split(",")
    if ext.strip()
)

# Per-file size cap, 50 MB by default (tunable via UPLOAD_MAX_MB).
MAX_FILE_BYTES = int(os.environ.get("UPLOAD_MAX_MB", "50")) * 1024 * 1024


def extension_blocked(filename: str) -> bool:
    """True when the file's extension is on the upload blocklist."""
    ext = Path(filename).suffix.lower().lstrip(".")
    return ext in BLOCKED_EXTENSIONS


async def save_uploaded_files(challenge_id: int, files: list[UploadFile] | None) -> list[str]:
    """Store author files via the active backend (app/storage.py).

    Local disk by default (``data/challenges/<id>/originals/``);
    Backblaze B2 when B2_* is configured. Every file passes three
    gates (brief §13):
    1. sanitized filename (no paths / weird characters)
    2. extension blocklist (executables & active scripts -> 400)
    3. size cap while streaming (default 50 MB -> 413)

    Returns the saved (sanitized) filenames. A rejected file leaves
    nothing partial behind.
    """
    from app.storage import get_storage

    return await get_storage().save_files(challenge_id, files)


def delete_uploaded_file(challenge_id: int, filename: str) -> None:
    """Delete ONE already-uploaded file via the active backend.

    The admin's delete button on the challenge edit form. The name
    must already be its sanitized form (anything else is ignored) —
    callers get the names from `list_original_files`, never from raw
    user input.
    """
    from app.storage import get_storage

    get_storage().delete_file(challenge_id, filename)
