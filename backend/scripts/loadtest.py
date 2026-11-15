"""Controlled latency / concurrency measurement against a RUNNING, ISOLATED AIMO backend.

Never point this at production or at a database you care about: it logs in repeatedly and creates
procurement requests. Intended target: the E2E stack (`frontend/e2e/start-backend.mjs`, mock Valio).

Usage (from backend/):
  python -m scripts.loadtest --base http://localhost:8100 --password <E2E demo password> [--pid <uvicorn pid>]
Prints one JSON document with environment, sample sizes, percentiles, throughput, errors and RSS.
"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

QUERIES = ["lactose free milk", "butter", "464201", "gluten-free pasta", "maito", "juusto", "kerma", "oat drink",
           "coffee", "rice"]


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))], 1)


def summary(ms: list[float], errors: int, wall: float) -> dict:
    return {"n": len(ms) + errors, "ok": len(ms), "errors": errors, "p50_ms": pct(ms, 50), "p95_ms": pct(ms, 95),
            "p99_ms": pct(ms, 99), "max_ms": round(max(ms), 1), "mean_ms": round(statistics.mean(ms), 1),
            "throughput_rps": round(len(ms) / wall, 1)}


def rss_mb(pid: int | None) -> float | None:
    if not pid:
        return None
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return round(int(out) / 1024, 1) if out else None


def run(fn, n: int, concurrency: int) -> dict:
    ms, errors = [], 0

    def one(i):
        t = time.perf_counter()
        ok = fn(i)
        return ok, (time.perf_counter() - t) * 1000

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for ok, dt in pool.map(one, range(n)):
            if ok:
                ms.append(dt)
            else:
                errors += 1
    return {"concurrency": concurrency, **summary(ms, errors, time.perf_counter() - t0)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8100")
    ap.add_argument("--password", default=None, help="or set LOADTEST_PASSWORD (preferred: keeps it out of argv)")
    ap.add_argument("--buyer", default="buyer@aimo.local")
    ap.add_argument("--viewer", default="viewer@aimo.local")
    ap.add_argument("--login-emails", default="admin@aimo.local,manager@aimo.local,buyer@aimo.local,buyer2@aimo.local,"
                    "viewer@aimo.local,buyer@nordic.local,manager@nordic.local")
    ap.add_argument("--pid", type=int)
    ap.add_argument("--label", default="")
    a = ap.parse_args()
    import os

    a.password = a.password or os.environ.get("LOADTEST_PASSWORD")
    if not a.password:
        ap.error("--password or LOADTEST_PASSWORD is required")
    c = httpx.Client(base_url=a.base, timeout=120, limits=httpx.Limits(max_connections=128, max_keepalive_connections=128))

    def token(email):
        r = c.post("/api/auth/login", json={"email": email, "password": a.password})
        r.raise_for_status()
        return {"Authorization": f"Bearer {r.json()['token']}"}

    buyer, viewer = token(a.buyer), token(a.viewer)
    res: dict = {"label": a.label, "target": a.base, "python": platform.python_version(), "machine": platform.machine(),
                 "rss_mb_start": rss_mb(a.pid), "tests": {}}
    for _ in range(10):  # warm-up (not measured)
        c.get("/api/products", params={"q": "milk"}, headers=viewer)

    get = lambda path, h, **kw: (lambda i: c.get(path, headers=h, **kw).status_code == 200)  # noqa: E731
    search = lambda i: c.get("/api/products", params={"q": QUERIES[i % len(QUERIES)]}, headers=viewer).status_code == 200  # noqa: E731
    T = res["tests"]
    T["health_seq"] = run(get("/health", {}), 200, 1)
    T["auth_me_seq"] = run(get("/api/auth/me", viewer), 200, 1)
    T["search_seq"] = run(search, 200, 1)
    T["request_list_seq"] = run(get("/api/procurement/requests", buyer, params={"page_size": 20}), 100, 1)
    for conc in (8, 32, 64):
        T[f"search_c{conc}"] = run(search, 400, conc)
    T["auth_me_c32"] = run(get("/api/auth/me", viewer), 400, 32)

    emails = [e.strip() for e in a.login_emails.split(",") if e.strip()]
    login = lambda i: c.post("/api/auth/login", json={"email": emails[i % len(emails)], "password": a.password}).status_code == 200  # noqa: E731
    T["login_seq"] = run(login, 30, 1)
    conc = 2 * len(emails)  # 2 in flight per account: below the per-account+source throttle (5)
    T[f"login_c{conc}"] = run(login, 10 * len(emails), conc)

    nonce = int(time.time())
    procure = lambda i: c.post("/api/procurement/requests", headers=buyer,  # noqa: E731
                               json={"text": f"We need {20 + (i % 50)} kg of unsalted butter for Wednesday, run {nonce}-{i}"}).status_code == 200
    T["procure_seq"] = run(procure, 20, 1)
    T["procure_c8"] = run(procure, 40, 8)

    # Mixed workload: 70 % search, 20 % auth/me, 10 % procurement writes
    def mixed(i):
        k = i % 10
        if k < 7:
            return search(i)
        if k < 9:
            return c.get("/api/auth/me", headers=viewer).status_code == 200
        return procure(10_000 + i)

    T["mixed_c32"] = run(mixed, 600, 32)
    res["rss_mb_end"] = rss_mb(a.pid)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
