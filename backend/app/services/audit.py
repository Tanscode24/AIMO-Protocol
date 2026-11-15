"""Audit trail: append-only through the application, secret-redacted, and tamper-EVIDENT.

Tamper evidence: each event is "sealed" with a monotonically increasing `seq`, the previous event's
hash and SHA-256(prev_hash + canonical event content). `verify_chain()` recomputes the chain and reports
the first event whose content or linkage no longer matches. This detects edits/deletions of individual
events. It is NOT immutability: someone with direct database write access could rewrite the entire
chain. Mitigation: export the head hash (`GET /api/audit/verify`) to an external system periodically.

Sealing happens after each successful commit that wrote audit events (rolled-back events never enter the
chain). It runs on a separate connection pool (see `database.side_engine`) so it can never deadlock the
request pool, under a process-wide lock plus a database-level write lock (SQLite BEGIN IMMEDIATE /
PostgreSQL advisory lock), so several worker processes extend one consistent chain.
"""
from __future__ import annotations

import hashlib
import logging
import json
import threading
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.context import get_correlation_id
from app.models import AuditEvent
from app.security import Principal, redact

log = logging.getLogger("app.audit")
_SEAL_LOCK = threading.Lock()
GENESIS = "0" * 64

SYSTEM = Principal(id="system", name="AIMO system", role="SYSTEM", kind="SYSTEM", organization_id=None)

# Human-readable labels for the audit dashboard
ACTION_LABELS = {
    "REQUEST_RECEIVED": "Agent request received",
    "INTENT_EXTRACTED": "Intent extracted",
    "CATALOGUE_SEARCHED": "Catalogue searched",
    "PRODUCTS_SHORTLISTED": "Products shortlisted",
    "UNIT_CONVERSION_CHECKED": "Units and lot sizes checked",
    "PRICES_RETRIEVED": "Prices retrieved",
    "AVAILABILITY_RETRIEVED": "Availability retrieved",
    "SUBSTITUTIONS_EVALUATED": "Substitutions evaluated",
    "RECOMMENDATION_GENERATED": "Recommendation generated",
    "POLICY_EVALUATED": "Policy evaluated",
    "DUPLICATE_DETECTED": "Duplicate request detected",
    "APPROVAL_REQUESTED": "Approval requested",
    "APPROVAL_GRANTED": "Approval granted",
    "APPROVAL_REJECTED": "Approval rejected",
    "CHANGES_REQUESTED": "Changes requested",
    "REVALIDATION_PERFORMED": "Price and availability revalidated",
    "REVALIDATION_FAILED": "Revalidation failed",
    "PROPOSAL_GENERATED": "Procurement proposal generated",
    "REQUEST_BLOCKED": "Request blocked",
    "POLICY_UPDATED": "Policy updated",
    "POLICY_CHANGE_DENIED": "Policy change denied",
    "USER_LOGIN": "User signed in",
    "LIVE_CHECK": "Live price/availability check",
    "API_HEALTH_CHECK": "Valio API health check",
    "ACCESS_DENIED": "Access denied",
    "AGENT_CREATED": "Agent identity created",
    "AGENT_KEY_ISSUED": "Agent API key issued",
    "AGENT_UPDATED": "Agent identity updated",
    "AGENT_KEY_REVOKED": "Agent API key revoked",
    "AGENT_RATE_LIMITED": "Agent rate limited",
    "USER_CREATED": "User created",
    "USER_UPDATED": "User updated",
    "AUDIT_CHAIN_VERIFIED": "Audit chain verified",
    "CATALOGUE_INGESTED": "Catalogue ingested",
}


def record(
    db: Session,
    action: str,
    actor: Principal | None = None,
    *,
    request_id: str | None = None,
    product_id: str | None = None,
    decision: str | None = None,
    reason: str | None = None,
    evidence: list[Any] | None = None,
    payload: dict[str, Any] | None = None,
    organization_id: str | None = None,
    policy_version: int | None = None,
    commit: bool = False,
) -> AuditEvent:
    actor = actor or SYSTEM
    ev = AuditEvent(
        ts=datetime.now(timezone.utc),
        actor_type=actor.kind,
        actor_id=actor.id,
        actor_name=actor.name,
        action=action,
        request_id=request_id,
        product_id=product_id,
        decision=decision,
        reason=redact(reason) if reason else None,
        evidence_json=json.dumps(redact(evidence or []), default=str),
        payload_json=json.dumps(redact(payload or {}), default=str),
        organization_id=organization_id or actor.organization_id,
        policy_version=policy_version,
        correlation_id=get_correlation_id(),
    )
    db.add(ev)
    if commit:
        db.commit()
    else:
        db.flush()
    return ev


# ------------------------------------------------------------------ hash chain
def _canon_ts(ts) -> str:
    if ts is None:
        return ""
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%f")


_FIELDS = ("id", "ts", "actor_type", "actor_id", "actor_name", "action", "request_id", "product_id", "decision",
           "reason", "evidence_json", "payload_json", "organization_id", "policy_version", "correlation_id")


def event_hash(row: dict, seq: int, prev_hash: str) -> str:
    content = {k: (_canon_ts(row.get(k)) if k == "ts" else row.get(k)) for k in _FIELDS}
    blob = json.dumps({"seq": seq, "prev": prev_hash, "event": content}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


_PG_SEAL_LOCK_ID = 0x41494D4F  # "AIMO": transaction-scoped advisory lock serialising sealing across processes


def rechain_unhashed(conn: Connection) -> int:
    """Seal all unsealed events in (ts, id) order, extending the existing chain."""
    cols = ", ".join(_FIELDS)
    with _SEAL_LOCK:
        if conn.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _PG_SEAL_LOCK_ID})
        last = conn.execute(text("SELECT seq, hash FROM audit_events WHERE seq IS NOT NULL ORDER BY seq DESC LIMIT 1")).first()
        seq, prev = (last[0], last[1]) if last else (0, GENESIS)
        rows = conn.execute(text(f"SELECT {cols} FROM audit_events WHERE seq IS NULL ORDER BY ts, id")).mappings().all()
        for row in rows:
            seq += 1
            h = event_hash(dict(row), seq, prev)
            conn.execute(text("UPDATE audit_events SET seq = :s, prev_hash = :p, hash = :h WHERE id = :i"),
                         {"s": seq, "p": prev, "h": h, "i": row["id"]})
            prev = h
        return len(rows)


def seal_pending(bind) -> int:
    from app.database import side_engine

    with side_engine(bind).begin() as conn:
        return rechain_unhashed(conn)


def verify_chain(db: Session) -> dict:
    seal_pending(db.get_bind())
    cols = ", ".join(_FIELDS + ("seq", "prev_hash", "hash"))
    rows = db.execute(text(f"SELECT {cols} FROM audit_events ORDER BY seq")).mappings().all()
    prev, expected_seq = GENESIS, 1
    for row in rows:
        problem = None
        if row["seq"] != expected_seq:
            problem = f"sequence gap: expected {expected_seq}, found {row['seq']} (event deleted or inserted)"
        elif row["prev_hash"] != prev:
            problem = "prev_hash does not match the previous event (chain broken)"
        elif event_hash(dict(row), row["seq"], prev) != row["hash"]:
            problem = "event content does not match its hash (event modified)"
        if problem:
            return {"ok": False, "events_checked": expected_seq - 1, "first_invalid_event": row["id"],
                    "first_invalid_seq": row["seq"], "problem": problem, "head_hash": None}
        prev, expected_seq = row["hash"], expected_seq + 1
    return {"ok": True, "events_checked": len(rows), "head_hash": prev if rows else GENESIS,
            "first_invalid_event": None, "problem": None,
            "guarantee": "Tamper-evident hash chain (SHA-256). Not immutable storage: export head_hash externally to "
                         "detect a full-chain rewrite."}


_DIRTY = "aimo_audit_dirty"


@event.listens_for(Session, "after_flush")
def _mark_audit_dirty(session: Session, _ctx) -> None:  # pragma: no cover - exercised by API tests
    if any(isinstance(o, AuditEvent) for o in session.new):
        session.info[_DIRTY] = True


@event.listens_for(Session, "after_rollback")
def _clear_audit_dirty(session: Session) -> None:  # pragma: no cover - exercised by API tests
    session.info.pop(_DIRTY, None)


@event.listens_for(Session, "after_commit")
def _seal_after_commit(session: Session) -> None:  # pragma: no cover - exercised by API tests
    # Only commits that wrote audit events need sealing (any older unsealed rows are sealed with them and
    # by verify_chain). Previously every commit — logins, sessions, throttling — opened a sealing transaction.
    if session.info.pop(_DIRTY, False):
        try:
            seal_pending(session.get_bind())
        except Exception:
            log.warning("Audit sealing deferred (will be retried on the next audit commit)", exc_info=True)
    try:
        from app.services.integration_status import flush_pending

        flush_pending(session.get_bind())
    except Exception:
        pass


def to_dict(ev: AuditEvent) -> dict:
    return {
        "id": ev.id,
        "ts": ev.ts.isoformat() if ev.ts else None,
        "actor_type": ev.actor_type,
        "actor_id": ev.actor_id,
        "actor_name": ev.actor_name,
        "action": ev.action,
        "action_label": ACTION_LABELS.get(ev.action, ev.action.replace("_", " ").title()),
        "request_id": ev.request_id,
        "product_id": ev.product_id,
        "decision": ev.decision,
        "reason": ev.reason,
        "evidence": json.loads(ev.evidence_json or "[]"),
        "payload": json.loads(ev.payload_json or "{}"),
        "organization_id": ev.organization_id,
        "policy_version": ev.policy_version,
        "correlation_id": ev.correlation_id,
        "seq": ev.seq,
        "hash": ev.hash,
        "prev_hash": ev.prev_hash,
    }
