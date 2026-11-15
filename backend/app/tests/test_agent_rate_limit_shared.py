"""The agent gateway rate limit must hold across worker processes (uvicorn --workers N), not per process."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from app.tests.conftest import AGENT

BACKEND = Path(__file__).resolve().parents[2]

WORKER = """
import sys
from app.database import SessionLocal
from app.services.rate_limit import MemoryRateLimiter, db_check
backend, attempts, limit, bucket = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
mem = MemoryRateLimiter()
allowed = 0
for _ in range(attempts):
    if backend == "memory":
        ok = mem.check(bucket, limit) is None
    else:
        with SessionLocal() as db:
            ok = db_check(db, bucket, limit) is None
    allowed += ok
print("ALLOWED", allowed)
"""


@pytest.fixture(autouse=True)
def _clean(client):
    from app.api.agent_gateway import limiter

    limiter.reset()
    yield
    limiter.reset()


def _workers(backend: str, procs: int, attempts: int, limit: int, bucket: str) -> int:
    env = {**os.environ, "PYTHONPATH": str(BACKEND)}
    ps = [subprocess.Popen([sys.executable, "-c", WORKER, backend, str(attempts), str(limit), bucket], cwd=BACKEND,
                           env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(procs)]
    total = 0
    for p in ps:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err[-800:]
        total += int(out.split("ALLOWED")[1])
    return total


def test_database_limit_is_shared_across_worker_processes(client):
    # 3 "workers" x 15 requests against a limit of 20/min. The guarantee is "never more than the limit".
    # Under true parallelism (PostgreSQL) a few concurrently-rejected hits are briefly counted by others before
    # they delete themselves, so slightly fewer may pass (observed 18/20); SQLite serialises writes (20/20).
    allowed = _workers("database", 3, 15, 20, "agent:mp-test")
    assert 10 <= allowed <= 20, allowed


def test_memory_backend_is_per_process_which_is_why_production_rejects_it(client):
    # Documents the defect being fixed: each process allows the full limit (3 x 15 = 45 > 20).
    assert _workers("memory", 3, 15, 20, "agent:mp-mem") == 45


def test_concurrent_threads_cannot_exceed_shared_limit(client):
    from app.database import SessionLocal
    from app.services.rate_limit import db_check

    n, limit, results, barrier = 30, 10, [], threading.Barrier(30)

    def hit():
        barrier.wait()
        with SessionLocal() as db:
            results.append(db_check(db, "agent:thread-test", limit) is None)

    ts = [threading.Thread(target=hit) for _ in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(results) == n and 1 <= results.count(True) <= limit


def test_window_slides(client):
    from app.database import SessionLocal
    from app.services.rate_limit import WINDOW_SECONDS, db_check

    t0 = time.time()
    with SessionLocal() as db:
        assert all(db_check(db, "agent:slide", 2, now=t0) is None for _ in range(2))
        retry = db_check(db, "agent:slide", 2, now=t0 + 1)
        assert retry is not None and 1 <= retry <= WINDOW_SECONDS
        assert db_check(db, "agent:slide", 2, now=t0 + WINDOW_SECONDS + 1) is None


def test_gateway_uses_database_backend_by_default(client):
    from app.config import get_settings
    from app.database import SessionLocal
    from app.models import RateLimitHit

    assert get_settings().agent_rate_limit_backend == "database"
    assert client.get("/api/agent/v1/me", headers=AGENT).status_code == 200
    with SessionLocal() as db:
        assert db.query(RateLimitHit).filter(RateLimitHit.bucket.like("agent:%")).count() >= 1


def test_production_rejects_memory_backend():
    from app.config import Settings
    import secrets

    s = Settings(_env_file=None, app_env="production", auth_secret=secrets.token_urlsafe(48), session_cookie_secure=True,
                 seed_demo_users=False, agent_api_key="", agent_rate_limit_backend="memory")
    assert any("AGENT_RATE_LIMIT_BACKEND" in p for p in s.runtime_security_problems())
