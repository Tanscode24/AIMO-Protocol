"""Database concurrency regression tests (reliability sprint).

Root cause being guarded: the `after_commit` audit-sealing hook checked out a SECOND connection from the
request pool while the committing session still held its first one. With more concurrent committers than
pool connections (5 + 10 overflow) every holder waited for another holder → all requests stalled for the
30 s pool timeout. Sealing now uses a separate pool (`database.side_engine`) and only runs for commits that
wrote audit events; it takes a database write lock so several processes extend ONE chain.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from sqlalchemy import func, text

BACKEND = Path(__file__).resolve().parents[2]
THREADS = 40  # well above the request pool's 15-connection ceiling


def _run_threads(n, fn):
    barrier, out = threading.Barrier(n), []

    def wrap(i):
        barrier.wait()
        t = time.perf_counter()
        try:
            fn(i)
            out.append(("ok", time.perf_counter() - t))
        except Exception as e:  # pragma: no cover - reported by the assertion
            out.append((f"{type(e).__name__}: {e}"[:200], time.perf_counter() - t))

    threads = [threading.Thread(target=wrap, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    return out


def _verify(db):
    from app.services.audit import verify_chain

    return verify_chain(db)


def test_side_engine_is_a_separate_pool(client):
    from app.database import engine, side_engine

    side = side_engine(engine)
    assert side is not engine and side.pool is not engine.pool
    assert side_engine(engine) is side  # cached


def test_concurrent_audit_commits_do_not_stall_and_keep_chain_valid(client):
    from app.database import SessionLocal
    from app.models import AuditEvent
    from app.services import audit

    with SessionLocal() as db:
        before = db.query(func.count(AuditEvent.id)).scalar()

    def write(i):
        with SessionLocal() as db:
            audit.record(db, "CONCURRENCY_TEST", reason=f"thread {i}", commit=True)

    t0 = time.perf_counter()
    results = _run_threads(THREADS, write)
    elapsed = time.perf_counter() - t0
    errors = [r for r in results if r[0] != "ok"]
    assert not errors, errors[:3]
    assert len(results) == THREADS
    assert max(r[1] for r in results) < 10, f"slowest commit {max(r[1] for r in results):.1f}s (stall regression)"
    assert elapsed < 20
    with SessionLocal() as db:
        assert db.query(func.count(AuditEvent.id)).scalar() == before + THREADS
        v = _verify(db)
        assert v["ok"], v
        unsealed = db.execute(text("SELECT count(*) FROM audit_events WHERE seq IS NULL")).scalar()
        dupes = db.execute(text("SELECT count(*) FROM (SELECT seq FROM audit_events GROUP BY seq HAVING count(*) > 1)")).scalar()
        assert unsealed == 0 and dupes == 0


def test_concurrent_http_logins_do_not_stall(client):
    """Each login writes a session row, throttle rows and an audit event (sealed after commit).
    Load is spread over 7 accounts x 3: in-flight attempts count toward the per-account+source limit (5) by
    design (that is what makes the throttle race-safe), so >5 simultaneous logins of ONE account would 429."""
    from app.database import SessionLocal
    from app.services.login_throttle import reset_all

    codes = []

    from app.seed import DEMO_USERS

    emails = [u[0] for u in DEMO_USERS]

    def login(i):
        r = client.post("/api/auth/login", json={"email": emails[i % len(emails)], "password": "test-password"})
        codes.append(r.status_code)

    results = _run_threads(21, login)
    client.cookies.clear()
    with SessionLocal() as s:
        reset_all(s)
    assert all(r[0] == "ok" for r in results), results[:3]
    assert codes.count(200) == 21, codes
    assert max(r[1] for r in results) < 15


def test_multiple_worker_processes_extend_one_chain(client):
    """Three OS processes (like uvicorn --workers 3) write audit events at the same time."""
    from app.database import SessionLocal
    from app.models import AuditEvent

    with SessionLocal() as db:
        before = db.query(func.count(AuditEvent.id)).scalar()
    code = ("import sys\nfrom app.database import SessionLocal\nfrom app.services import audit\n"
            "for i in range(15):\n"
            "    with SessionLocal() as db:\n"
            "        audit.record(db, 'MULTIPROCESS_TEST', reason=f'p{sys.argv[1]} e{i}', commit=True)\n")
    env = {**os.environ, "PYTHONPATH": str(BACKEND)}
    procs = [subprocess.Popen([sys.executable, "-c", code, str(n)], cwd=BACKEND, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for n in range(3)]
    outs = [p.communicate(timeout=120) for p in procs]
    assert all(p.returncode == 0 for p in procs), [o[1][-800:] for o in outs]
    with SessionLocal() as db:
        assert db.query(func.count(AuditEvent.id)).scalar() == before + 45
        v = _verify(db)
        assert v["ok"], v
        dupes = db.execute(text("SELECT count(*) FROM (SELECT seq FROM audit_events GROUP BY seq HAVING count(*) > 1)")).scalar()
        assert dupes == 0
