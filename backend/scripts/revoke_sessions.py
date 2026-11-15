"""Emergency session revocation (incident response). Revoked tokens stop working on the very next request.

Usage (from backend/):
  python -m scripts.revoke_sessions --user someone@example.com     # one user's sessions
  python -m scripts.revoke_sessions --all --yes                     # every active user session
Agent API keys are separate: revoke or rotate them in Agents & Access (or POST /api/admin/agents/.../revoke).
Rotating AUTH_SECRET additionally invalidates every token signature (all users must sign in again).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from app.database import SessionLocal
from sqlalchemy import func

from app.models import AuthSession, User
from app.services import audit, sessions


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--user")
    g.add_argument("--all", action="store_true")
    ap.add_argument("--yes", action="store_true", help="required with --all")
    ap.add_argument("--reason", default="INCIDENT_RESPONSE")
    a = ap.parse_args()
    with SessionLocal() as db:
        if a.all:
            if not a.yes:
                sys.exit("Refusing to revoke ALL sessions without --yes")
            per_org = dict(db.query(User.organization_id, func.count(AuthSession.id))
                           .join(User, User.id == AuthSession.user_id)
                           .filter(AuthSession.revoked_at.is_(None)).group_by(User.organization_id).all())
            n = (db.query(AuthSession).filter(AuthSession.revoked_at.is_(None))
                 .update({AuthSession.revoked_at: datetime.now(timezone.utc), AuthSession.revoked_reason: a.reason[:60]},
                         synchronize_session=False))
            target = "ALL users"
        else:
            user = db.query(User).filter(User.email == a.user.strip().lower()).first()
            if not user:
                sys.exit(f"No user {a.user}")
            n = sessions.revoke_user_sessions(db, user.id, a.reason[:60], commit=False)
            per_org = {user.organization_id: n}
            target = user.email
        # One event per affected organization, so each tenant's administrators see it in their audit trail.
        for org, count in (per_org or {None: 0}).items():
            audit.record(db, "SESSIONS_REVOKED", organization_id=org,
                         reason=f"Operator revoked {count} session(s) ({'all users' if a.all else target})",
                         payload={"count": count, "scope": "ALL" if a.all else "USER", "reason": a.reason[:60]})
        db.commit()
        print(f"Revoked {n} active session(s) for {target}.")


if __name__ == "__main__":
    main()
