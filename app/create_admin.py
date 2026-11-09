"""Create the FIRST admin account from the command line.

The admin portal has no "sign up as admin" path (a player role is the
only thing registration can create), so the very first admin must be
made here, like CTFd's create_admin:

    python -m app.create_admin username email@example.com
    admin_password: (interactive prompt, or ADMIN_PASSWORD env var)

Run only when needed: a second admin already inside the portal can
change roles from the users page.
"""

import argparse
import getpass
import os
import re
import sys

from sqlalchemy import func

from app.database import SessionLocal, init_db
from app.models import User
from app.security import hash_password

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,64}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def create_admin(username: str, email: str, password: str) -> User:
    """Create (or refuse on duplicate) an admin and return the row."""
    init_db()  # ensure the schema exists even on a fresh install
    db = SessionLocal()
    try:
        if db.query(User).filter(func.lower(User.username) == username.lower()).first():
            raise SystemExit(f"Error: username '{username}' is already taken.")
        if db.query(User).filter(User.email == email.lower()).first():
            raise SystemExit(f"Error: email '{email}' is already registered.")
        admin = User(username=username, email=email.lower(),
                     password_hash=hash_password(password), role="admin")
        db.add(admin)
        db.commit()
        return admin
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create the first BYTE_BATALLION_CTF_PLATFORM admin account.")
    parser.add_argument("username", help="admin username (3-64 letters/digits/underscore)")
    parser.add_argument("email", help="admin email address")
    args = parser.parse_args(argv)

    if not USERNAME_RE.fullmatch(args.username):
        print("Error: username must be 3-64 characters (letters, digits, underscores).",
              file=sys.stderr)
        raise SystemExit(2)
    if not EMAIL_RE.fullmatch(args.email):
        print("Error: enter a valid email address.", file=sys.stderr)
        raise SystemExit(2)

    # Password from ADMIN_PASSWORD env var (script/Docker-friendly) or a
    # hidden prompt. It is hashed immediately and never printed back.
    password = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("admin password: ")
    if len(password) < 8:
        print("Error: password must be at least 8 characters.", file=sys.stderr)
        raise SystemExit(2)

    admin = create_admin(args.username, args.email, password)
    print(f"Admin created: #{admin.id} {admin.username} ({admin.email}) role=admin")
    print(f"Log in at /auth/login with the password you just set.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
