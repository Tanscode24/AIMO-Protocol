"""Operational readiness: migration failure atomicity, backup/restore, bootstrap & incident scripts, secret rotation.
Runs against the suite's database (SQLite by default, PostgreSQL via AIMO_TEST_DATABASE_URL)."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

BACKEND = Path(__file__).resolve().parents[2]


def _run(module: str, *args: str, password: str | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(BACKEND)}
    if password:
        env["AIMO_NEW_USER_PASSWORD"] = password
    return subprocess.run([sys.executable, "-m", module, *args], cwd=BACKEND, env=env, capture_output=True, text=True,
                          timeout=120, stdin=subprocess.DEVNULL)


def test_failed_migration_rolls_back_atomically(client, monkeypatch):
    """A migration that fails half-way must leave no partial schema change and must not be recorded."""
    from app import migrations
    from app.database import engine

    with engine.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS ops_mig_probe"))
        c.execute(text("CREATE TABLE ops_mig_probe (id INTEGER PRIMARY KEY)"))

    def broken(conn):
        conn.execute(text("ALTER TABLE ops_mig_probe ADD COLUMN half_done VARCHAR(10)"))
        conn.execute(text("INSERT INTO ops_mig_probe (id, half_done) VALUES (1, 'x')"))
        raise RuntimeError("simulated failure in the middle of a migration")

    monkeypatch.setattr(migrations, "MIGRATIONS", migrations.MIGRATIONS + [("999_broken_probe", broken)])
    with pytest.raises(RuntimeError):
        migrations.run_migrations(engine)
    with engine.connect() as c:
        cols = {col["name"] for col in inspect(c).get_columns("ops_mig_probe")}
        rows = c.execute(text("SELECT count(*) FROM ops_mig_probe")).scalar()
        recorded = c.execute(text("SELECT count(*) FROM schema_migrations WHERE id = '999_broken_probe'")).scalar()
    assert "half_done" not in cols and rows == 0 and recorded == 0
    with engine.begin() as c:
        c.execute(text("DROP TABLE ops_mig_probe"))
    monkeypatch.setattr(migrations, "MIGRATIONS", migrations.MIGRATIONS[:-1])
    assert migrations.run_migrations(engine) == []  # the real migrations remain fully applied


def test_sqlite_online_backup_restores_a_consistent_auditable_copy(client, tmp_path):
    from app.database import engine, is_sqlite
    from app.services import audit

    if not is_sqlite(engine):
        pytest.skip("SQLite backup API; PostgreSQL uses pg_dump/pg_restore (validated in docs/FINAL_QA_AUDIT.md §18)")
    from app.database import SessionLocal

    with SessionLocal() as db:
        audit.record(db, "BACKUP_PROBE", reason="before backup", commit=True)
    src = sqlite3.connect(engine.url.database)
    dst_path = tmp_path / "restored.db"
    dst = sqlite3.connect(dst_path)
    src.backup(dst)  # online, consistent even while the WAL is active
    src.close()
    assert dst.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    dst.close()
    from sqlalchemy.orm import Session

    restored = create_engine(f"sqlite:///{dst_path}")
    with Session(restored) as s:
        v = audit.verify_chain(s)
        assert v["ok"] and v["events_checked"] > 0
        assert s.execute(text("SELECT count(*) FROM audit_events WHERE action = 'BACKUP_PROBE'")).scalar() >= 1
    restored.dispose()


def test_create_user_script_bootstraps_and_resets_with_session_revocation(client):
    email = "ops-admin@example.test"
    r = _run("scripts.create_user", "--email", email, "--name", "Ops Admin", "--role", "ADMIN", password="ops-initial-pass-1")
    assert r.returncode == 0, r.stderr[-500:]
    assert "ops-initial-pass-1" not in r.stdout + r.stderr
    login = client.post("/api/auth/login", json={"email": email, "password": "ops-initial-pass-1"})
    client.cookies.clear()
    assert login.status_code == 200 and login.json()["user"]["role"] == "ADMIN"
    token = login.json()["token"]
    assert _run("scripts.create_user", "--email", email, "--name", "X", "--role", "ADMIN", password="ops-initial-pass-1").returncode != 0
    assert _run("scripts.create_user", "--email", email, "--reset-password", password="short").returncode != 0
    r = _run("scripts.create_user", "--email", email, "--reset-password", password="ops-rotated-pass-2")
    assert r.returncode == 0 and "1 active session(s) revoked" in r.stdout, r.stdout + r.stderr
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": email, "password": "ops-rotated-pass-2"}).status_code == 200
    client.cookies.clear()


def test_revoke_sessions_script_for_incident_response(client, admin):
    from app.tests.conftest import login

    victim = login(client, "buyer2@aimo.local")
    other = login(client, "viewer@aimo.local")
    r = _run("scripts.revoke_sessions", "--user", "buyer2@aimo.local", "--reason", "TEST_INCIDENT")
    assert r.returncode == 0 and "Revoked" in r.stdout, r.stderr[-500:]
    assert client.get("/api/auth/me", headers=victim).status_code == 401
    assert client.get("/api/auth/me", headers=other).status_code == 200  # targeted
    assert _run("scripts.revoke_sessions", "--all").returncode != 0  # refuses without --yes
    r = _run("scripts.revoke_sessions", "--all", "--yes")
    assert r.returncode == 0
    assert client.get("/api/auth/me", headers=other).status_code == 401
    assert client.get("/api/auth/me", headers=admin).status_code == 401
    events = client.get("/api/audit", params={"action": "SESSIONS_REVOKED"}, headers=login(client, "admin@aimo.local")).json()
    assert events["total"] >= 2


def test_auth_secret_rotation_invalidates_every_existing_token(client, monkeypatch):
    from pydantic import SecretStr

    from app.config import get_settings
    from app.tests.conftest import login

    old = login(client, "viewer@aimo.local")
    assert client.get("/api/auth/me", headers=old).status_code == 200
    monkeypatch.setattr(get_settings(), "auth_secret", SecretStr("rotated-" + "k" * 56))
    assert client.get("/api/auth/me", headers=old).status_code == 401  # signature no longer valid
    new = login(client, "viewer@aimo.local")
    assert client.get("/api/auth/me", headers=new).status_code == 200
