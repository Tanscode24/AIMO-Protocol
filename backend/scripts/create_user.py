"""Create (or reset the password of) a user — the supported way to bootstrap the first ADMIN in production,
where SEED_DEMO_USERS must be false.

The password is NEVER taken from the command line (it would land in shell history / process listings):
it is read from the AIMO_NEW_USER_PASSWORD environment variable, or prompted for interactively.

Usage (from backend/):
  python -m scripts.create_user --email admin@example.com --name "Ops Admin" --role ADMIN [--org org_default]
  python -m scripts.create_user --email admin@example.com --reset-password
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from app.database import SessionLocal, init_db
from app.models import Organization, User
from app.security import ADMIN, BUYER, MANAGER, VIEWER, hash_password
from app.services import sessions

ROLES = {"ADMIN": ADMIN, "PROCUREMENT_MANAGER": MANAGER, "BUYER": BUYER, "VIEWER": VIEWER}


def _password() -> str:
    pw = os.environ.get("AIMO_NEW_USER_PASSWORD") or getpass.getpass("New password (min 12 chars): ")
    if len(pw) < 12:
        sys.exit("Password must be at least 12 characters.")
    return pw


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--name")
    ap.add_argument("--role", choices=sorted(ROLES))
    ap.add_argument("--org", default="org_default")
    ap.add_argument("--org-name", default=None, help="create the organization with this name if it does not exist")
    ap.add_argument("--reset-password", action="store_true", help="reset an existing user's password (revokes their sessions)")
    a = ap.parse_args()
    init_db()
    email = a.email.strip().lower()
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).first()
        if a.reset_password:
            if not user:
                sys.exit(f"No user {email}")
            user.password_hash = hash_password(_password())
            n = sessions.revoke_user_sessions(db, user.id, "PASSWORD_RESET", commit=False)
            db.commit()
            print(f"Password reset for {email}; {n} active session(s) revoked.")
            return
        if user:
            sys.exit(f"User {email} already exists (use --reset-password).")
        if not (a.name and a.role):
            sys.exit("--name and --role are required to create a user.")
        if not db.get(Organization, a.org):
            if not a.org_name:
                sys.exit(f"Organization {a.org} does not exist; pass --org-name to create it.")
            db.add(Organization(id=a.org, name=a.org_name))
        role = ROLES[a.role]
        db.add(User(email=email, name=a.name, role=role, password_hash=hash_password(_password()), organization_id=a.org,
                    read_scope="OWN" if role == BUYER else "ORG"))
        db.commit()
        print(f"Created {a.role} {email} in {a.org}.")


if __name__ == "__main__":
    main()
