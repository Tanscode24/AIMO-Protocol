"""Record-level authorization and tenant isolation.

All record visibility is decided here, server-side, from the authenticated principal
(role, organization and read scope are loaded from the database on every request).

Visibility of a procurement request (and of its recommendations, approvals, proposals and audit events):
  1. The record must belong to the principal's organization (tenant isolation, always).
  2. read_scope ORG (Admin, Approver, explicitly authorised Viewer): every record in the organization.
  3. read_scope OWN (Buyer):   only requests the user created.
  4. AGENT:                    only requests the agent itself created through the agent API.

Mutations are stricter than reads:
  - Choosing/re-deciding a recommendation: only the request owner (creating user or creating agent).
  - Approval decisions: permission 'approve' + same organization + never the requester, never the
    owner of the agent that created the request (separation of duties) + humans only.

Invisible records are answered with 404 (no existence oracle) and an ACCESS_DENIED audit event.
Lists are filtered in SQL so pagination never leaks counts of foreign records.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Query, Session

from app.models import Agent, Approval, AuditEvent, Order, ProcurementRequest
from app.security import Principal


def _deny(db: Session, principal: Principal, kind: str, record_id: str, why: str) -> HTTPException:
    from app.services import audit

    audit.record(db, "ACCESS_DENIED", principal, decision="DENIED",
                 reason=f"{principal.kind} {principal.name} ({principal.role}) denied access to {kind} {record_id}: {why}",
                 payload={"record_type": kind, "record_id": record_id}, commit=True)
    return HTTPException(404, f"{kind.capitalize()} not found")


def request_query(db: Session, principal: Principal) -> Query:
    q = db.query(ProcurementRequest).filter(ProcurementRequest.organization_id == principal.organization_id)
    if principal.kind == "AGENT":
        return q.filter(ProcurementRequest.agent_id == principal.id, ProcurementRequest.created_by.is_(None))
    if principal.read_scope == "ORG":
        return q
    return q.filter(ProcurementRequest.created_by == principal.id)


def visible_request_ids(db: Session, principal: Principal):
    return request_query(db, principal).with_entities(ProcurementRequest.id)


def can_view_request(principal: Principal, req: ProcurementRequest) -> tuple[bool, str]:
    if req.organization_id != principal.organization_id:
        return False, "different organization"
    if principal.kind == "AGENT":
        ok = req.agent_id == principal.id and req.created_by is None
        return ok, "" if ok else "agent may only access requests it created"
    if principal.read_scope == "ORG":
        return True, ""
    ok = req.created_by == principal.id
    return ok, "" if ok else "not the owner of this request"


def is_owner(principal: Principal, req: ProcurementRequest) -> bool:
    if req.organization_id != principal.organization_id:
        return False
    if principal.kind == "AGENT":
        return req.agent_id == principal.id and req.created_by is None
    return req.created_by == principal.id


def get_request_for(db: Session, principal: Principal, request_id: str) -> ProcurementRequest:
    req = db.get(ProcurementRequest, request_id)
    if not req:
        raise HTTPException(404, "Request not found")
    ok, why = can_view_request(principal, req)
    if not ok:
        raise _deny(db, principal, "request", request_id, why)
    return req


def get_request_for_owner(db: Session, principal: Principal, request_id: str) -> ProcurementRequest:
    req = get_request_for(db, principal, request_id)
    if not is_owner(principal, req):
        from app.services import audit

        audit.record(db, "ACCESS_DENIED", principal, request_id=req.id, decision="DENIED",
                     reason=f"{principal.name} may view but not modify request {req.id} (not the owner)", commit=True)
        raise HTTPException(403, "Only the owner of a request can change its selected option")
    return req


def approval_query(db: Session, principal: Principal) -> Query:
    return db.query(Approval).filter(Approval.request_id.in_(visible_request_ids(db, principal)))


def get_approval_for(db: Session, principal: Principal, approval_id: str) -> Approval:
    a = db.get(Approval, approval_id)
    if not a:
        raise HTTPException(404, "Approval not found")
    req = db.get(ProcurementRequest, a.request_id)
    ok, why = can_view_request(principal, req)
    if not ok:
        raise _deny(db, principal, "approval", approval_id, why)
    return a


def check_can_decide_approval(db: Session, principal: Principal, req: ProcurementRequest) -> None:
    """Separation of duties for approvals (in addition to the 'approve' permission)."""
    if principal.kind != "USER":
        raise HTTPException(403, "Only human users can decide approvals")
    if req.organization_id != principal.organization_id:
        raise HTTPException(404, "Approval not found")
    if req.created_by and req.created_by == principal.id:
        raise HTTPException(403, "Separation of duties: you cannot decide on a request you created")
    if req.created_by is None and req.agent_id:
        agent = db.get(Agent, req.agent_id)
        if agent and agent.owner_user_id == principal.id:
            raise HTTPException(403, "Separation of duties: you own the agent that created this request")


def order_query(db: Session, principal: Principal) -> Query:
    return db.query(Order).filter(Order.request_id.in_(visible_request_ids(db, principal)))


def get_order_for(db: Session, principal: Principal, order_id: str) -> Order:
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, "Proposal not found")
    req = db.get(ProcurementRequest, o.request_id)
    ok, why = can_view_request(principal, req)
    if not ok:
        raise _deny(db, principal, "proposal", order_id, why)
    return o


def audit_query(db: Session, principal: Principal) -> Query:
    q = db.query(AuditEvent).filter(AuditEvent.organization_id == principal.organization_id)
    if principal.kind == "USER" and principal.read_scope == "ORG":
        return q
    return q.filter(or_(AuditEvent.request_id.in_(visible_request_ids(db, principal)),
                        AuditEvent.actor_id == principal.id))


def get_audit_for(db: Session, principal: Principal, event_id: str) -> AuditEvent:
    e = db.get(AuditEvent, event_id)
    if not e:
        raise HTTPException(404, "Event not found")
    visible = audit_query(db, principal).filter(AuditEvent.id == event_id).first()
    if not visible:
        raise _deny(db, principal, "audit event", event_id, "outside the principal's audit scope")
    return e


def scoped_idempotency_key(principal: Principal, key: str | None) -> str | None:
    """Idempotency keys are namespaced per organization and principal so they can never collide
    across tenants or return another principal's record."""
    if not key:
        return None
    return f"{principal.organization_id}:{principal.id}:{key}"[:160]
