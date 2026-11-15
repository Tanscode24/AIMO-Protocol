"""Server-side, revocable user sessions.

Each login creates an `AuthSession` row; the signed token carries its id (`sid`). On every authenticated
request `resolve_session()` re-checks the row, so logout, deactivation and password changes take effect
immediately for both the HttpOnly cookie and any copied bearer token — not only when the token expires.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import AuthSession, User

REVOKED_LOGOUT = "LOGOUT"
REVOKED_DEACTIVATED = "USER_DEACTIVATED"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite returns naive UTC values


def password_fingerprint(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode()).hexdigest()


def create_session(db: Session, user: User, ttl_minutes: int, user_agent: str | None = None) -> AuthSession:
    now = _now()
    sess = AuthSession(id=f"ses_{secrets.token_hex(16)}", user_id=user.id, created_at=now,
                       expires_at=now + timedelta(minutes=ttl_minutes),
                       password_fingerprint=password_fingerprint(user.password_hash),
                       user_agent=(user_agent or "")[:200] or None)
    db.add(sess)
    # Opportunistic clean-up of long-expired rows keeps the table small without a scheduler.
    db.query(AuthSession).filter(AuthSession.expires_at < now - timedelta(days=7)).delete(synchronize_session=False)
    db.commit()
    return sess


def resolve_session(db: Session, session_id: str | None, user_id: str | None) -> User | None:
    """The active user behind a session, or None if the session is unknown, revoked, expired, belongs to a
    different user, predates a password change, or the user is inactive."""
    if not session_id or not user_id:
        return None
    sess = db.get(AuthSession, session_id)
    if sess is None or sess.revoked_at is not None or sess.user_id != user_id:
        return None
    if _aware(sess.expires_at) <= _now():
        return None
    user = db.get(User, user_id)
    if user is None or not user.active:
        return None
    if not hmac.compare_digest(sess.password_fingerprint, password_fingerprint(user.password_hash)):
        return None
    return user


def revoke_session(db: Session, session_id: str, reason: str = REVOKED_LOGOUT) -> bool:
    sess = db.get(AuthSession, session_id)
    if sess is None or sess.revoked_at is not None:
        return False
    sess.revoked_at, sess.revoked_reason = _now(), reason
    db.commit()
    return True


def revoke_user_sessions(db: Session, user_id: str, reason: str, commit: bool = True) -> int:
    n = (db.query(AuthSession)
         .filter(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
         .update({AuthSession.revoked_at: _now(), AuthSession.revoked_reason: reason}, synchronize_session=False))
    if commit:
        db.commit()
    return n
