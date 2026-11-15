"""Sliding-window request rate limiting for the agent gateway.

Backends (AGENT_RATE_LIMIT_BACKEND):
  database (default) — hits are rows in the shared application database (`rate_limit_hits`), so the limit
                       is enforced ONCE across all uvicorn workers / hosts, and survives restarts.
  memory             — per-process deques. Only correct with a single worker process; rejected in production
                       because N workers would silently allow N x the configured limit.

Race safety (database): a hit is inserted and committed BEFORE counting, so the k-th committed hit always
sees >= k rows and concurrent requests cannot all slip under the limit. A rejected hit removes its own row,
so a throttled client does not extend its own block (same as the previous in-memory behaviour).
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import RateLimitHit

WINDOW_SECONDS = 60


class MemoryRateLimiter:
    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, now: float | None = None) -> int | None:
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > WINDOW_SECONDS:
                q.popleft()
            if len(q) >= limit:
                return max(1, int(WINDOW_SECONDS - (now - q[0])) + 1)
            q.append(now)
        return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def db_check(db: Session, bucket: str, limit: int, now: float | None = None) -> int | None:
    """Returns None if allowed, else Retry-After seconds."""
    now = time.time() if now is None else now
    cutoff = now - WINDOW_SECONDS
    db.query(RateLimitHit).filter(RateLimitHit.ts < now - 2 * WINDOW_SECONDS).delete(synchronize_session=False)
    hit = RateLimitHit(bucket=bucket, ts=now)
    db.add(hit)
    db.commit()  # reserve first, count second
    count, oldest = db.query(func.count(RateLimitHit.id), func.min(RateLimitHit.ts)).filter(
        RateLimitHit.bucket == bucket, RateLimitHit.ts >= cutoff).one()
    if count > limit:
        db.delete(hit)
        db.commit()
        return max(1, int((oldest or now) + WINDOW_SECONDS - now) + 1)
    return None


def db_reset(db: Session) -> None:
    db.query(RateLimitHit).delete(synchronize_session=False)
    db.commit()


class RateLimiter:
    """Facade used by the gateway; the backend is chosen per call from settings."""

    def __init__(self):
        self.memory = MemoryRateLimiter()

    def check(self, key: str, limit: int, db: Session | None = None, backend: str = "database") -> int | None:
        if backend == "memory" or db is None:
            return self.memory.check(key, limit)
        return db_check(db, f"agent:{key}", limit)

    def reset(self) -> None:
        self.memory.reset()
        from app.database import SessionLocal

        with SessionLocal() as db:
            db_reset(db)
