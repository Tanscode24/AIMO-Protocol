"""Login brute-force throttling backed by the shared application database.

Why the database: it is already shared by every worker process (SQLite file on one host, or PostgreSQL),
so limits hold across workers without adding Redis. Three sliding-window limits apply to *failed* attempts:

  * account + source  (LOGIN_MAX_FAILURES_PER_ACCOUNT_IP)  — normal user mistyping / single attacker
  * account, any source (LOGIN_MAX_FAILURES_PER_ACCOUNT)   — attacker rotating IP addresses
  * source, any account (LOGIN_MAX_FAILURES_PER_IP)        — password spraying across accounts

Concurrency: an attempt is *reserved* (row inserted and committed) BEFORE the password is checked, and
only then are rows counted. The k-th committed reservation therefore always sees at least k rows, so
parallel requests cannot all slip under the limit. Rejected (429) attempts delete their reservation so
throttled retries do not extend the window; successful logins delete theirs and reset the account's
failures. Nothing is permanent: rows simply age out of the window, so the limiter cannot be used to
lock an account forever (see docs/SECURITY.md for the residual availability trade-off).

Account and source identifiers are stored only as HMAC digests. The same limits apply whether or not
the email exists, so throttling reveals nothing about account existence.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import LoginAttempt


@dataclass
class Reservation:
    attempt_id: int | None
    account_key: str
    source_key: str
    retry_after: int | None = None  # set when throttled

    @property
    def throttled(self) -> bool:
        return self.retry_after is not None


def client_source(request: Request, trusted_proxy_count: int, trusted_proxy_ips: set[str] | None = None) -> str:
    """Client address. X-Forwarded-For is honoured only when (a) the deployment declares trusted proxies AND
    (b) the TCP peer itself is one of the trusted proxy addresses; then only the entry appended by the
    outermost trusted proxy is used (clients can forge everything to its left)."""
    peer = request.client.host if request.client else "unknown"
    if trusted_proxy_count > 0 and peer in (trusted_proxy_ips or set()):
        hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
        if len(hops) >= trusted_proxy_count:
            return hops[-trusted_proxy_count]
    return request.client.host if request.client else "unknown"


def _digest(settings: Settings, kind: str, value: str) -> str:
    key = settings.auth_secret.get_secret_value().encode()
    return hmac.new(key, f"login:{kind}:{value}".encode(), hashlib.sha256).hexdigest()


def normalize_account(email: str) -> str:
    return email.strip().lower()


def reserve(db: Session, settings: Settings, email: str, source: str, now: float | None = None) -> Reservation:
    account_key = _digest(settings, "account", normalize_account(email))
    source_key = _digest(settings, "source", source)
    if not settings.login_rate_limit_enabled:
        return Reservation(None, account_key, source_key)
    now = time.time() if now is None else now
    window = settings.login_window_seconds
    cutoff = now - window
    db.query(LoginAttempt).filter(LoginAttempt.ts < now - 2 * window).delete(synchronize_session=False)
    attempt = LoginAttempt(account_key=account_key, source_key=source_key, ts=now)
    db.add(attempt)
    db.commit()  # reserve first, count second (see module docstring)

    checks = (
        ((LoginAttempt.account_key == account_key, LoginAttempt.source_key == source_key),
         settings.login_max_failures_per_account_ip),
        ((LoginAttempt.account_key == account_key,), settings.login_max_failures_per_account),
        ((LoginAttempt.source_key == source_key,), settings.login_max_failures_per_ip),
    )
    retry_after: int | None = None
    for conds, limit in checks:
        # The current reservation is included, so `limit` failures are allowed and attempt limit+1 is refused.
        count, oldest = db.query(func.count(LoginAttempt.id), func.min(LoginAttempt.ts)).filter(
            LoginAttempt.ts >= cutoff, *conds).one()
        if count > limit:
            wait = max(1, int((oldest or now) + window - now) + 1)
            retry_after = max(retry_after or 0, wait)
    if retry_after is not None:
        db.delete(attempt)
        db.commit()
        return Reservation(None, account_key, source_key, retry_after)
    return Reservation(attempt.id, account_key, source_key)


def record_success(db: Session, res: Reservation) -> None:
    """Reset policy: a successful login clears this account's failures (from every source). Failures this
    source made against OTHER accounts are kept, so a valid account cannot launder a spraying source."""
    db.query(LoginAttempt).filter(LoginAttempt.account_key == res.account_key).delete(synchronize_session=False)
    db.commit()


def reset_all(db: Session) -> None:
    """Test/ops helper: clear all throttling state."""
    db.query(LoginAttempt).delete(synchronize_session=False)
    db.commit()
