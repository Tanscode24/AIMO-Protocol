"""Observed Valio API connectivity (not inferred from key presence).

Every Valio call (ping / price / availability) is recorded as an IntegrationEvent with status,
HTTP code and latency. Connectivity states:
  NOT_CONFIGURED          no VALIO_API_KEY
  CONFIGURED_UNVERIFIED   key present, but no real (non-simulated) call observed yet
  CONNECTED               most recent real call succeeded
  AUTH_FAILED             most recent real call was rejected (401/403)
  DEGRADED                most recent real call failed (timeout, 429, 5xx, malformed)
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import IntegrationEvent, new_id

log = logging.getLogger("app.integration")


_PENDING: list[dict] = []
_PENDING_LOCK = threading.Lock()


def record_call(operation: str, status: str, *, http_status: int | None = None, latency_ms: int | None = None,
                products: int | None = None, error: str | None = None, simulated: bool = False) -> None:
    """Buffer the event in memory. It is persisted after the caller's transaction commits
    (see audit._after_commit) so it never competes for SQLite's write lock mid-transaction."""
    with _PENDING_LOCK:
        _PENDING.append({"id": new_id("int"), "operation": operation, "status": status, "http_status": http_status,
                         "latency_ms": latency_ms, "products": products, "error": (error or "")[:500] or None,
                         "simulated": simulated, "ts": datetime.now(timezone.utc)})


def flush_pending(bind) -> int:
    with _PENDING_LOCK:
        items = list(_PENDING)
        _PENDING.clear()
    if not items:
        return 0
    try:
        from app.database import side_engine  # separate pool: may run inside after_commit (see side_engine)

        with side_engine(bind).begin() as conn:
            conn.execute(IntegrationEvent.__table__.insert(), items)
    except Exception as e:  # never let observability break procurement
        log.warning("Could not persist integration events: %s", type(e).__name__)
    return len(items)


def _iso(ts):
    return ts.isoformat() if ts else None


def connectivity(db: Session) -> dict:
    flush_pending(db.get_bind())
    s = get_settings()
    real = db.query(IntegrationEvent).filter(IntegrationEvent.simulated.is_(False), IntegrationEvent.status != "NOT_CONFIGURED")
    last = real.order_by(IntegrationEvent.ts.desc()).first()
    last_ok = real.filter(IntegrationEvent.status == "OK").order_by(IntegrationEvent.ts.desc()).first()
    if not s.live_api_configured:
        state, detail = "NOT_CONFIGURED", "VALIO_API_KEY is not set — demo mode, nothing is verified live"
    elif last is None:
        state, detail = "CONFIGURED_UNVERIFIED", "API key present but no call has been made yet — press Ping in the Trust Center"
    elif last.status == "OK":
        state, detail = "CONNECTED", f"Last {last.operation} call succeeded"
    elif last.status == "AUTH_ERROR":
        state, detail = "AUTH_FAILED", f"Last {last.operation} call rejected the API key (HTTP {last.http_status})"
    else:
        state, detail = "DEGRADED", f"Last {last.operation} call failed: {last.status} {last.error or ''}".strip()
    return {"state": state, "detail": detail, "last_call_at": _iso(last.ts if last else None),
            "last_success_at": _iso(last_ok.ts if last_ok else None)}


def summary(db: Session) -> dict:
    base = db.query(IntegrationEvent).filter(IntegrationEvent.simulated.is_(False))
    out = {"connectivity": connectivity(db), "operations": {}}
    for op in ("ping", "price", "availability"):
        q = base.filter(IntegrationEvent.operation == op)
        last = q.order_by(IntegrationEvent.ts.desc()).first()
        last_ok = q.filter(IntegrationEvent.status == "OK").order_by(IntegrationEvent.ts.desc()).first()
        avg = q.filter(IntegrationEvent.status == "OK", IntegrationEvent.latency_ms.isnot(None)) \
            .with_entities(func.avg(IntegrationEvent.latency_ms)).scalar()
        counts = dict(q.with_entities(IntegrationEvent.status, func.count()).group_by(IntegrationEvent.status).all())
        out["operations"][op] = {
            "last_status": last.status if last else None, "last_at": _iso(last.ts if last else None),
            "last_http_status": last.http_status if last else None, "last_error": last.error if last else None,
            "last_success_at": _iso(last_ok.ts if last_ok else None),
            "last_latency_ms": last.latency_ms if last else None,
            "avg_latency_ms": round(avg) if avg else None, "counts": counts,
        }
    out["simulated_events"] = db.query(IntegrationEvent).filter(IntegrationEvent.simulated.is_(True)).count()
    return out
