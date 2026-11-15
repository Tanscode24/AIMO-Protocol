"""Reproducible, idempotent schema migrations.

`init_db()` first runs `create_all` (creates any missing *tables*), then this module adds missing
*columns* to existing tables and back-fills data. Every migration is recorded in
`schema_migrations` and is safe to run repeatedly. Existing records are preserved.

Run explicitly:  python -m scripts.migrate     (also runs automatically at backend start-up)
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

log = logging.getLogger("app.migrations")

DEFAULT_ORG = "org_default"


def _has_column(conn: Connection, table: str, column: str) -> bool:
    return any(c["name"] == column for c in inspect(conn).get_columns(table))


def _add_column(conn: Connection, table: str, column: str, ddl: str) -> None:
    if not _has_column(conn, table, column):
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        log.info("Added column %s.%s", table, column)


def m001_organizations(conn: Connection) -> None:
    for table in ("users", "agents", "policies", "procurement_requests", "orders"):
        _add_column(conn, table, "organization_id", f"VARCHAR(40) DEFAULT '{DEFAULT_ORG}'")
        conn.execute(text(f"UPDATE {table} SET organization_id = :o WHERE organization_id IS NULL"), {"o": DEFAULT_ORG})
    _add_column(conn, "users", "read_scope", "VARCHAR(10) DEFAULT 'OWN'")
    # Pre-existing roles that were organisation-wide keep that visibility explicitly
    conn.execute(text("UPDATE users SET read_scope = 'ORG' WHERE role IN ('ADMIN', 'PROCUREMENT_MANAGER', 'VIEWER') "
                      "AND (read_scope IS NULL OR read_scope = 'OWN')"))
    conn.execute(text("UPDATE users SET read_scope = 'OWN' WHERE read_scope IS NULL"))
    _add_column(conn, "agents", "description", "TEXT")
    exists = conn.execute(text("SELECT 1 FROM organizations WHERE id = :o"), {"o": DEFAULT_ORG}).first()
    if not exists:
        conn.execute(text("INSERT INTO organizations (id, name, created_at) VALUES (:o, :n, CURRENT_TIMESTAMP)"),
                     {"o": DEFAULT_ORG, "n": "Demo Kitchen Group"})


def m002_request_fields(conn: Connection) -> None:
    _add_column(conn, "procurement_requests", "overrides_json", "TEXT DEFAULT '{}'")
    _add_column(conn, "procurement_requests", "confirmed_assumptions", "BOOLEAN DEFAULT 0")
    _add_column(conn, "procurement_requests", "correlation_id", "VARCHAR(80)")


def m003_audit_chain(conn: Connection) -> None:
    for col, ddl in (("organization_id", "VARCHAR(40)"), ("policy_version", "INTEGER"), ("correlation_id", "VARCHAR(80)"),
                     ("seq", "INTEGER"), ("prev_hash", "VARCHAR(64)"), ("hash", "VARCHAR(64)")):
        _add_column(conn, "audit_events", col, ddl)
    # Back-fill organization for historical events from their request, else default org
    conn.execute(text(
        "UPDATE audit_events SET organization_id = COALESCE((SELECT r.organization_id FROM procurement_requests r "
        "WHERE r.id = audit_events.request_id), :o) WHERE organization_id IS NULL"), {"o": DEFAULT_ORG})
    # Back-fill the hash chain for events created before chaining existed
    from app.services.audit import rechain_unhashed

    rechain_unhashed(conn)


def m004_auth_sessions_and_login_throttle(conn: Connection) -> None:
    """Additive only: server-side sessions (revocable logout) and login-attempt throttling tables.

    `create_all` normally creates them first; this re-check keeps the migration self-contained and idempotent
    when run against a database whose tables were created by an older code version. Nothing is dropped.
    Tokens issued before this migration carry no session id and are rejected, so users sign in once more."""
    from app.models import AuthSession, LoginAttempt

    for model in (AuthSession, LoginAttempt):
        model.__table__.create(bind=conn, checkfirst=True)


def m005_shared_rate_limit(conn: Connection) -> None:
    """Additive only: shared (cross-worker) agent rate-limit table. Nothing is dropped or rewritten."""
    from app.models import RateLimitHit

    RateLimitHit.__table__.create(bind=conn, checkfirst=True)


def m006_pg_trigram_search(conn: Connection) -> None:
    """PostgreSQL only (no-op on SQLite, which uses FTS5): trigram GIN indexes for the ILIKE catalogue search.
    Measured on 17,546 products, 4 workers: sequential search p50 53 ms -> 6.7 ms; 8 concurrent clients
    110 -> 517 req/s. Additive and idempotent. If the role may not create the pg_trgm extension (it is a
    "trusted" extension since PostgreSQL 13, so a database owner normally can), the migration logs a warning
    and skips the indexes instead of failing the deployment. CREATE INDEX briefly blocks writes to `products`
    (~3 s measured), which only the catalogue ingestion performs."""
    if conn.dialect.name != "postgresql":
        return
    try:
        with conn.begin_nested():
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    except Exception as e:  # noqa: BLE001 - privilege problems must not block the deployment
        log.warning("pg_trgm unavailable (%s); catalogue search will use sequential ILIKE scans", type(e).__name__)
        return
    for col in ("name_en", "name_fi", "brand"):
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_products_{col}_trgm ON products USING gin ({col} gin_trgm_ops)"))


MIGRATIONS: list[tuple[str, Callable[[Connection], None]]] = [
    ("001_organizations_and_read_scope", m001_organizations),
    ("002_request_overrides_and_correlation", m002_request_fields),
    ("003_audit_hash_chain", m003_audit_chain),
    ("004_auth_sessions_and_login_throttle", m004_auth_sessions_and_login_throttle),
    ("005_shared_rate_limit", m005_shared_rate_limit),
    ("006_pg_trigram_search", m006_pg_trigram_search),
]


def run_migrations(engine: Engine) -> list[str]:
    """All pending migrations run in ONE transaction: a failure leaves no partial change and nothing recorded.
    SQLite: Python's sqlite3 driver does not open a transaction before DDL (ALTER/CREATE would autocommit), so
    migrations run on an engine that starts every transaction with BEGIN IMMEDIATE (transactional DDL + the
    write lock). Verified by test_operations.py::test_failed_migration_rolls_back_atomically."""
    applied: list[str] = []
    if engine.url.get_backend_name() == "sqlite":
        from app.database import side_engine

        engine = side_engine(engine)
    with engine.begin() as conn:
        done = {r[0] for r in conn.execute(text("SELECT id FROM schema_migrations"))}
        for mid, fn in MIGRATIONS:
            if mid in done:
                continue
            fn(conn)
            conn.execute(text("INSERT INTO schema_migrations (id, applied_at) VALUES (:i, CURRENT_TIMESTAMP)"), {"i": mid})
            applied.append(mid)
            log.info("Applied migration %s", mid)
    return applied
