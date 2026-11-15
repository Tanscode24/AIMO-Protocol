"""Database engine and session management.

SQLite by default. Set DATABASE_URL to a PostgreSQL URL to migrate; the only
SQLite-specific feature is the FTS5 search index, which has a LIKE fallback.
"""
from __future__ import annotations

import threading
from collections.abc import Generator, Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


SQLITE_BUSY_TIMEOUT_SECONDS = 30


def make_engine(url: str, *, immediate_transactions: bool = False, **pool_kwargs) -> Engine:
    """`immediate_transactions` (SQLite only): every transaction starts with BEGIN IMMEDIATE, i.e. it takes
    the database write lock up-front (waiting up to the busy timeout) instead of failing with
    "database is locked" when a deferred read transaction later tries to upgrade to a write."""
    kwargs: dict = {"future": True, **pool_kwargs}
    sqlite = url.startswith("sqlite")
    if sqlite:
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": SQLITE_BUSY_TIMEOUT_SECONDS}
    else:
        kwargs.setdefault("pool_pre_ping", True)
    eng = create_engine(url, **kwargs)
    if sqlite:

        @event.listens_for(eng, "connect")
        def _pragma(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
            if immediate_transactions:
                dbapi_conn.isolation_level = None  # let SQLAlchemy's "begin" event emit the BEGIN

        if immediate_transactions:

            @event.listens_for(eng, "begin")
            def _begin(conn):  # pragma: no cover - trivial
                conn.exec_driver_sql("BEGIN IMMEDIATE")

    return eng


def _pool_kwargs() -> dict:
    s = get_settings()
    return {"pool_size": s.db_pool_size, "max_overflow": s.db_max_overflow,
            "pool_timeout": s.db_pool_timeout_seconds, "pool_recycle": s.db_pool_recycle_seconds}


engine = make_engine(get_settings().database_url, **_pool_kwargs())
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


_SIDE_ENGINES: dict[str, Engine] = {}
_SIDE_LOCK = threading.Lock()


def side_engine(bind) -> Engine:
    """A small, separate connection pool for post-commit bookkeeping (audit sealing, integration events).

    Why: SQLAlchemy fires `after_commit` while the committing session still holds its pooled connection.
    Checking out a SECOND connection from the same pool there deadlocks once every pooled connection is
    held by a session waiting for another one (observed: all requests stall until the 30 s pool timeout
    with ~15 concurrent writers). A separate pool breaks that cycle: its users never wait on the main pool.
    In-memory SQLite cannot be shared across engines, so it falls back to the original bind."""
    eng = bind.engine if hasattr(bind, "engine") else bind
    url = eng.url
    if url.get_backend_name() == "sqlite" and url.database in (None, "", ":memory:"):
        return eng
    key = url.render_as_string(hide_password=False)
    with _SIDE_LOCK:
        side = _SIDE_ENGINES.get(key)
        if side is None:
            side = make_engine(key, immediate_transactions=True, pool_size=2, max_overflow=0)
            _SIDE_ENGINES[key] = side
        return side


_STARTUP_LOCK_KEY = 0x41494D30  # PostgreSQL advisory lock id for start-up bootstrap


@contextmanager
def startup_lock(eng: Engine | None = None) -> Iterator[None]:
    """Serialise start-up bootstrap (create tables, migrations, seed, first catalogue ingestion) across worker
    processes. Without it, N workers booting on an empty database race on CREATE TABLE and ingestion
    (observed with 4 workers on PostgreSQL: 3 crashed with unique violations). PostgreSQL: session-level
    advisory lock on a dedicated connection. SQLite file: an exclusive flock next to the database file."""
    target = eng or engine
    url = target.url
    if url.get_backend_name() == "postgresql":
        with target.connect() as conn:
            conn.exec_driver_sql(f"SELECT pg_advisory_lock({_STARTUP_LOCK_KEY})")
            conn.commit()
            try:
                yield
            finally:
                conn.exec_driver_sql(f"SELECT pg_advisory_unlock({_STARTUP_LOCK_KEY})")
                conn.commit()
    elif url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
        import fcntl

        with open(f"{url.database}.startup.lock", "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    else:
        yield


def dispose_all() -> None:
    """Close every pooled connection (graceful shutdown)."""
    engine.dispose()
    with _SIDE_LOCK:
        for side in _SIDE_ENGINES.values():
            side.dispose()


def database_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        return True
    except Exception:
        return False


def is_sqlite(eng: Engine | None = None) -> bool:
    return (eng or engine).url.get_backend_name() == "sqlite"


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(eng: Engine | None = None) -> list[str]:
    """Create missing tables, then apply idempotent column migrations (existing data preserved)."""
    from app import models  # noqa: F401  (register models)
    from app.migrations import run_migrations

    target = eng or engine
    Base.metadata.create_all(bind=target)
    return run_migrations(target)
