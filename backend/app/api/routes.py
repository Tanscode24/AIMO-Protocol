"""Internal AIMO REST API (not Valio endpoints). Documented automatically at /docs (OpenAPI)."""
from __future__ import annotations

import json
from datetime import datetime

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.integrations.valio_client import get_valio_client
from app.models import Approval, AuditEvent, Order, Policy, ProcurementRequest, Product, User, utcnow
from app.schemas import (ApprovalAction, DecideRequest, LiveCheckRequest, LoginRequest, ParseRequest, PolicyUpdate,
                         ProcurementCreate)
from app.security import (Principal, burn_password_check, clear_session_cookie, create_token, decode_token,
                          get_principal, presented_token, require, set_session_cookie, verify_password)
from app.seed import DEMO_USERS
from app.services import access, analytics, audit, integration_status, login_throttle, orchestrator, sessions
from app.services.catalogue import catalogue_count
from app.services.parser import parse_request
from app.services.policy import policy_snapshot
from app.services.search import product_detail, product_summary, search_catalogue
from app.services.units import convert_request
from app.services.verification import interpret_availability, interpret_price

router = APIRouter()
log = logging.getLogger("app.auth")

SCENARIOS = [
    {"id": "lactose-free-milk", "title": "Lactose-free milk, 1,000 L by Monday",
     "text": "I need 1,000 litres of lactose-free milk by Monday. Find the best option based on price and availability.",
     "demonstrates": "Parsing, unit conversion litres -> sales units, lot sizes, live price/availability, ranking, policy"},
    {"id": "allergen-kitchen", "title": "Allergen-sensitive kitchen",
     "text": "Allergen-sensitive kitchen: we need 20 kg of gluten-free pasta, it must be milk-free and egg-free, delivered by Friday.",
     "demonstrates": "Allergen exclusions (CONTAINS / MAY_CONTAIN / FREE_FROM), unknown allergen data is never treated as safe"},
    {"id": "spend-limit", "title": "Spending limit — human approval",
     "text": "We need 200 kg of unsalted butter for Wednesday, prioritise reliability.",
     "demonstrates": "Order value above the EUR 500 autonomous limit triggers REQUIRES_APPROVAL; packaging conflict detection"},
    {"id": "api-timeout", "title": "Valio API timeout (simulated)", "simulate": "timeout",
     "text": "Find lactose-free milk for 1000 litres delivered by Monday.",
     "demonstrates": "Live data cannot be verified -> never treated as available; approval required, no autonomous purchase"},
    {"id": "ambiguous", "title": "Ambiguous request — confirm assumptions",
     "text": "We need 40 unsalted butter",
     "demonstrates": "No unit and no date: the agent states its assumptions and will not buy autonomously until a human confirms"},
    {"id": "duplicate", "title": "Duplicate request protection",
     "text": "I need 1,000 litres of lactose-free milk by Monday. Find the best option based on price and availability.",
     "demonstrates": "Run twice: the second identical request is BLOCKED as a duplicate while the first is active"},
]


# ------------------------------------------------------------------ health & auth
@router.get("/health/live", tags=["System"])
def liveness():
    """Liveness probe: the process is up and serving (no dependency checks)."""
    return {"status": "ok"}


@router.get("/health", tags=["System"])
def health(db: Session = Depends(get_db)):
    """Readiness probe: 200 when the database answers, 503 otherwise (no details that could leak internals)."""
    from fastapi.responses import JSONResponse

    from app.database import database_reachable

    if not database_reachable():
        return JSONResponse(status_code=503, content={"status": "unavailable", "database": "unreachable",
                                                      "time": datetime.now().isoformat()})
    s = get_settings()
    conn = integration_status.connectivity(db)
    return {"status": "ok", "database": "ok", "time": datetime.now().isoformat(), "catalogue_products": catalogue_count(db),
            "live_api_configured": s.live_api_configured,
            # "mode" reflects configuration; "connectivity" reflects what has actually been observed.
            "mode": "LIVE" if s.live_api_configured else "DEMO",
            "connectivity": conn["state"], "connectivity_detail": conn["detail"],
            "last_successful_call": conn["last_success_at"],
            "llm_provider": s.llm_provider if s.llm_configured else "none (deterministic parser)"}


LOGIN_FAILED = "Invalid email or password"
LOGIN_THROTTLED = "Too many failed sign-in attempts. Please wait and try again."


@router.post("/api/auth/login", tags=["Auth"])
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    s = get_settings()
    source = login_throttle.client_source(request, s.trusted_proxy_count, s.trusted_proxy_ip_list)
    # Throttle BEFORE checking the password, identically for existing and unknown emails (no enumeration).
    res = login_throttle.reserve(db, s, body.email, source)
    if res.throttled:
        log.warning("Login throttled account=%s source=%s retry_after=%ss",
                    res.account_key[:12], res.source_key[:12], res.retry_after)
        raise HTTPException(429, LOGIN_THROTTLED, headers={"Retry-After": str(res.retry_after)})
    user = db.query(User).filter(User.email == login_throttle.normalize_account(body.email)).first()
    if user is None:
        burn_password_check(body.password)  # equalise timing with the known-account path
        ok = False
    else:
        ok = verify_password(body.password, user.password_hash) and user.active
    if not ok:
        log.info("Login failed account=%s source=%s", res.account_key[:12], res.source_key[:12])
        raise HTTPException(401, LOGIN_FAILED)
    login_throttle.record_success(db, res)
    p = Principal(id=user.id, name=user.name, role=user.role, kind="USER", email=user.email,
                  organization_id=user.organization_id, read_scope=user.read_scope)
    sess = sessions.create_session(db, user, s.token_ttl_minutes, request.headers.get("user-agent"))
    audit.record(db, "USER_LOGIN", p, reason=f"Role {user.role}", payload={"session_id": sess.id}, commit=True)
    token = create_token(user.id, user.role, sess.id)
    # Browser sessions: HttpOnly cookie (not readable by JavaScript). The token is also returned for API clients.
    set_session_cookie(response, token)
    return {"token": token,
            "user": {"id": user.id, "name": user.name, "email": user.email, "role": user.role,
                     "organization_id": user.organization_id}}


@router.post("/api/auth/logout", tags=["Auth"])
def logout(request: Request, response: Response, authorization: str | None = Header(default=None),
           db: Session = Depends(get_db)):
    """Revokes the presented session server-side (bearer token or cookie), then clears the cookie.
    Every copy of that session's token stops working. Other sessions of the same user are unaffected.
    Always returns ok, so it cannot be used to probe token validity."""
    token, _ = presented_token(request, authorization)
    payload = decode_token(token) if token else None
    if payload and payload.get("sid"):
        sid = str(payload["sid"])
        user = db.get(User, str(payload["sub"])) if payload.get("sub") else None
        if sessions.revoke_session(db, sid) and user is not None:
            p = Principal(id=user.id, name=user.name, role=user.role, kind="USER", email=user.email,
                          organization_id=user.organization_id, read_scope=user.read_scope)
            audit.record(db, "USER_LOGOUT", p, reason="Session revoked", payload={"session_id": sid}, commit=True)
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/api/auth/me", tags=["Auth"])
def me(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    from app.models import Organization

    org = db.get(Organization, principal.organization_id) if principal.organization_id else None
    return {"id": principal.id, "name": principal.name, "role": principal.role, "kind": principal.kind,
            "email": principal.email, "organization_id": principal.organization_id,
            "organization_name": org.name if org else None, "read_scope": principal.read_scope,
            "scopes": list(principal.scopes),
            "permissions": [k for k in ("view", "procure", "live_check", "approve", "edit_policy", "manage_identities",
                                        "view_trust", "verify_audit") if principal.can(k)]}


@router.get("/api/auth/demo-users", tags=["Auth"])
def demo_users():
    if not get_settings().seed_demo_users:
        return []
    return [{"email": e, "name": n, "role": r, "organization_id": o, "read_scope": sc} for e, n, r, o, sc in DEMO_USERS]


# ------------------------------------------------------------------ products
@router.get("/api/products", tags=["Catalogue"])
def list_products(
    q: str | None = Query(None, max_length=200), brand: str | None = None, category: str | None = None,
    vendor: str | None = None, allergen_status: str | None = None, claim: str | None = None,
    free_from: str | None = None, has_reliability: bool | None = None, has_carbon: bool | None = None,
    sales_unit: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(24, ge=1, le=100),
    db: Session = Depends(get_db), _: Principal = Depends(require("view")),
):
    return search_catalogue(db, q, brand=brand, category=category, vendor=vendor, allergen_status=allergen_status,
                            claim=claim, free_from=free_from, has_reliability=has_reliability, has_carbon=has_carbon,
                            sales_unit=sales_unit, page=page, page_size=page_size)


@router.get("/api/products/facets", tags=["Catalogue"])
def facets(db: Session = Depends(get_db), _: Principal = Depends(require("view"))):
    from sqlalchemy import func

    def top(col, n=40):
        return [{"value": v, "count": c} for v, c in db.query(col, func.count()).group_by(col).order_by(func.count().desc()).limit(n) if v]

    return {"sales_units": top(Product.sales_unit), "categories": top(Product.category, 60),
            "vendors": top(Product.vendor_name, 40), "brands": top(Product.brand, 40),
            "claims": ["FREE_FROM_LACTOSE", "FREE_FROM_GLUTEN", "VEGAN", "VEGETARIAN", "LOW_LACTOSE", "NO_ADDED_SUGARS",
                       "HIGH_PROTEIN", "LOW_FAT", "FREE_FROM_EGGS", "FREE_FROM_SOY"]}


@router.get("/api/products/{product_id}", tags=["Catalogue"])
def get_product(product_id: str, db: Session = Depends(get_db), _: Principal = Depends(require("view"))):
    p = db.get(Product, product_id)
    if not p:
        raise HTTPException(404, "Product not found")
    return product_detail(p)


@router.get("/api/products/{product_id}/alternatives", tags=["Catalogue"])
def alternatives(product_id: str, db: Session = Depends(get_db), _: Principal = Depends(require("view"))):
    from app.services.allergens import product_allergen_summary
    from app.services.search import fts_candidate_ids, match_score

    p = db.get(Product, product_id)
    if not p:
        raise HTTPException(404, "Product not found")
    base_alg = product_allergen_summary(p)
    out = []
    for sid in json.loads(p.substitutions_json or "[]"):
        sp = db.get(Product, sid)
        if sp:
            out.append({"source": "DATASET_SUBSTITUTION", **product_summary(sp)})
    query = " ".join((p.name_en or p.display_name).split()[:6])
    seen = {p.id} | {o["id"] for o in out}
    sims = []
    for pid in fts_candidate_ids(db, query, limit=300):
        if pid in seen:
            continue
        sp = db.get(Product, pid)
        if not sp or sp.category != p.category:
            continue
        m = match_score(sp, query)
        if m["score"] > 0:
            sims.append((m["score"], sp))
    sims.sort(key=lambda t: -t[0])
    for score, sp in sims[:8]:
        out.append({"source": "CATALOGUE_SIMILAR", "similarity": score, **product_summary(sp)})
    for o in out:
        sp = db.get(Product, o["id"])
        alg = product_allergen_summary(sp)
        o["comparison"] = {
            "same_sales_unit": sp.sales_unit == p.sales_unit,
            "same_category": sp.category == p.category,
            "allergen_data": alg["status"],
            "extra_allergens": sorted(set(alg["contains"]) - set(base_alg["contains"])),
            "removed_allergens": sorted(set(base_alg["contains"]) - set(alg["contains"])),
            "reliability_delta": (sp.reliability - p.reliability) if sp.reliability is not None and p.reliability is not None else None,
            "carbon_delta": (sp.carbon_kg_per_kg - p.carbon_kg_per_kg) if sp.carbon_kg_per_kg is not None and p.carbon_kg_per_kg is not None else None,
            "auto_substitution_safe": alg["status"] == "DECLARED" and base_alg["status"] == "DECLARED"
            and not (set(alg["contains"]) - set(base_alg["contains"])) and not (set(alg["may_contain"]) - set(base_alg["may_contain"])),
        }
    return {"product_id": product_id, "alternatives": out}


@router.post("/api/products/{product_id}/live-check", tags=["Catalogue"])
def live_check(product_id: str, body: LiveCheckRequest, db: Session = Depends(get_db),
               principal: Principal = Depends(require("live_check"))):
    p = db.get(Product, product_id)
    if not p:
        raise HTTPException(404, "Product not found")
    client = get_valio_client()
    pc = client.get_prices([p.id], body.requested_date)
    ac = client.get_availability([p.id], body.requested_date)
    conv = convert_request(p, body.quantity, "SALES_UNIT") if body.quantity else None
    qty = conv["quantity_sales_units"] if conv else None
    price = interpret_price(pc, p.id, body.requested_date)
    avail = interpret_availability(ac, p.id, body.requested_date, qty, p.sales_unit)
    audit.record(db, "LIVE_CHECK", principal, product_id=p.id, decision=f"{price['status']}/{avail['status']}",
                 reason=f"Manual check for {body.requested_date}", commit=True)
    return {"product_id": p.id, "price": price, "availability": avail, "quantity_sales_units": qty}


# ------------------------------------------------------------------ procurement
@router.get("/api/procurement/scenarios", tags=["Procurement"])
def scenarios(_: Principal = Depends(require("view"))):
    return SCENARIOS


@router.post("/api/procurement/parse", tags=["Procurement"])
def parse(body: ParseRequest, _: Principal = Depends(require("procure"))):
    intent, source, err = parse_request(body.text)
    return {"intent": json.loads(intent.model_dump_json()), "parser": source, "warning": err}


@router.post("/api/procurement/requests", tags=["Procurement"])
def create_request(body: ProcurementCreate, db: Session = Depends(get_db),
                   principal: Principal = Depends(require("procure")),
                   idempotency_key: str | None = Header(default=None, max_length=120)):
    req = orchestrator.run_procurement(db, body.text, principal, simulate=body.simulate, auto_decide=body.auto_decide,
                                       intent_override=body.intent_override,
                                       idempotency_key=access.scoped_idempotency_key(principal, idempotency_key),
                                       confirm_assumptions=body.confirm_assumptions)
    return orchestrator.decision_document(db, req)


@router.get("/api/procurement/requests", tags=["Procurement"])
def list_requests(status: str | None = None, decision: str | None = None, page: int = Query(1, ge=1),
                  page_size: int = Query(25, ge=1, le=100), db: Session = Depends(get_db),
                  principal: Principal = Depends(require("view"))):
    q = access.request_query(db, principal)
    if status:
        q = q.filter(ProcurementRequest.status == status)
    if decision:
        q = q.filter(ProcurementRequest.decision == decision)
    total = q.count()
    rows = q.order_by(ProcurementRequest.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = []
    for r in rows:
        intent = json.loads(r.parsed_json or "{}")
        sel = db.get(Product, r.selected_product_id) if r.selected_product_id else None
        items.append({"id": r.id, "created_at": r.created_at.isoformat(), "created_by": r.created_by_name,
                      "channel": r.channel, "text": r.raw_text, "status": r.status, "decision": r.decision,
                      "product_query": intent.get("display_query"), "quantity": intent.get("quantity"),
                      "unit": intent.get("unit"), "delivery_date": r.delivery_date,
                      "selected_product": {"id": sel.id, "name": sel.display_name} if sel else None,
                      "estimated_total_excl_tax": r.estimated_total_excl_tax, "currency": r.currency,
                      "simulate": r.simulate})
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.get("/api/procurement/requests/{request_id}", tags=["Procurement"])
def get_request(request_id: str, db: Session = Depends(get_db), principal: Principal = Depends(require("view"))):
    return orchestrator.decision_document(db, access.get_request_for(db, principal, request_id))


@router.post("/api/procurement/requests/{request_id}/decide", tags=["Procurement"])
def decide(request_id: str, body: DecideRequest, db: Session = Depends(get_db),
           principal: Principal = Depends(require("procure")),
           idempotency_key: str | None = Header(default=None, max_length=120)):
    req = access.get_request_for_owner(db, principal, request_id)
    if req.status in ("REJECTED",):
        raise HTTPException(409, "Request was rejected; create a new request")
    req = orchestrator.decide(db, req, body.product_id, principal,
                              idempotency_key=access.scoped_idempotency_key(principal, idempotency_key))
    return orchestrator.decision_document(db, req)


# ------------------------------------------------------------------ approvals
def approval_dict(db: Session, a: Approval) -> dict:
    req = db.get(ProcurementRequest, a.request_id)
    snap = json.loads(a.snapshot_json or "{}")
    return {"id": a.id, "request_id": a.request_id, "product_id": a.product_id, "status": a.status,
            "requested_at": a.requested_at.isoformat() if a.requested_at else None,
            "reasons": json.loads(a.reasons_json or "[]"), "decided_by": a.decided_by_name,
            "decided_at": a.decided_at.isoformat() if a.decided_at else None, "decision_reason": a.decision_reason,
            "previous_state": a.previous_state, "new_state": a.new_state, "consumed": a.consumed,
            "revalidation": json.loads(a.revalidation_json or "{}"),
            "request": {"text": req.raw_text, "created_by": req.created_by_name, "channel": req.channel,
                        "delivery_date": req.delivery_date, "intent": json.loads(req.parsed_json or "{}"),
                        "status": req.status, "policy_version": req.policy_version},
            "candidate": snap,
            "lifecycle": orchestrator.lifecycle(req, a, db.query(Order).filter(Order.request_id == a.request_id).first())}


@router.get("/api/approvals", tags=["Approvals"])
def list_approvals(status: str | None = None, db: Session = Depends(get_db), principal: Principal = Depends(require("view"))):
    q = access.approval_query(db, principal)
    if status:
        q = q.filter(Approval.status == status)
    return [approval_dict(db, a) for a in q.order_by(Approval.requested_at.desc()).limit(200).all()]


@router.get("/api/approvals/{approval_id}", tags=["Approvals"])
def get_approval(approval_id: str, db: Session = Depends(get_db), principal: Principal = Depends(require("view"))):
    return approval_dict(db, access.get_approval_for(db, principal, approval_id))


@router.post("/api/approvals/{approval_id}/approve", tags=["Approvals"])
def approve(approval_id: str, body: ApprovalAction, db: Session = Depends(get_db),
            principal: Principal = Depends(require("approve"))):
    return approval_dict(db, orchestrator.act_on_approval(db, approval_id, "approve", principal, body.reason))


@router.post("/api/approvals/{approval_id}/reject", tags=["Approvals"])
def reject(approval_id: str, body: ApprovalAction, db: Session = Depends(get_db),
           principal: Principal = Depends(require("approve"))):
    return approval_dict(db, orchestrator.act_on_approval(db, approval_id, "reject", principal, body.reason))


@router.post("/api/approvals/{approval_id}/request-changes", tags=["Approvals"])
def request_changes(approval_id: str, body: ApprovalAction, db: Session = Depends(get_db),
                    principal: Principal = Depends(require("approve"))):
    return approval_dict(db, orchestrator.act_on_approval(db, approval_id, "request_changes", principal, body.reason))


# ------------------------------------------------------------------ proposals / orders
def order_dict(o: Order) -> dict:
    return {"id": o.id, "request_id": o.request_id, "approval_id": o.approval_id, "kind": o.kind, "status": o.status,
            "submitted_to_supplier": o.submitted_to_supplier, "supplier_reference": o.supplier_reference,
            "policy_decision": o.policy_decision, "approval_status": o.approval_status,
            "total_excl_tax": o.total_excl_tax, "total_incl_tax": o.total_incl_tax, "currency": o.currency,
            "delivery_date": o.delivery_date, "lines": json.loads(o.lines_json or "[]"),
            "created_at": o.created_at.isoformat() if o.created_at else None, "created_by": o.created_by,
            "disclaimer": orchestrator.DISCLAIMER}


@router.get("/api/orders", tags=["Proposals"])
def list_orders(kind: str | None = None, db: Session = Depends(get_db), principal: Principal = Depends(require("view"))):
    q = access.order_query(db, principal)
    if kind:
        q = q.filter(Order.kind == kind)
    rows = q.order_by(Order.created_at.desc()).limit(200).all()
    return {"supplier_orders": [order_dict(o) for o in rows if o.kind == "SUPPLIER_ORDER"],
            "proposals": [order_dict(o) for o in rows if o.kind == "PROPOSAL"],
            "note": "The Valio Aimo API has no order endpoint, so no supplier orders can exist. Proposals are internal."}


@router.get("/api/orders/{order_id}", tags=["Proposals"])
def get_order(order_id: str, db: Session = Depends(get_db), principal: Principal = Depends(require("view"))):
    o = access.get_order_for(db, principal, order_id)
    events = access.audit_query(db, principal).filter(AuditEvent.request_id == o.request_id).order_by(AuditEvent.ts).all()
    req = db.get(ProcurementRequest, o.request_id)
    appr = db.get(Approval, o.approval_id) if o.approval_id else None
    return {**order_dict(o), "audit": [audit.to_dict(e) for e in events], "lifecycle": orchestrator.lifecycle(req, appr, o)}


# ------------------------------------------------------------------ policies
@router.get("/api/policies", tags=["Policies"])
def list_policies(db: Session = Depends(get_db), principal: Principal = Depends(require("view"))):
    return [policy_snapshot(p) for p in db.query(Policy).filter(Policy.organization_id == principal.organization_id).all()]


@router.get("/api/policies/active", tags=["Policies"])
def get_active_policy(db: Session = Depends(get_db), principal: Principal = Depends(require("view"))):
    return policy_snapshot(orchestrator.active_policy(db, principal.organization_id))


@router.put("/api/policies/{policy_id}", tags=["Policies"])
def update_policy(policy_id: str, body: PolicyUpdate, db: Session = Depends(get_db),
                  principal: Principal = Depends(get_principal)):
    if not principal.can("edit_policy"):
        audit.record(db, "POLICY_CHANGE_DENIED", principal, decision="DENIED",
                     reason=f"{principal.kind} {principal.name} ({principal.role}) attempted to modify policy {policy_id}",
                     payload=body.model_dump(exclude_none=True), commit=True)
        raise HTTPException(403, "Only administrators can change procurement policies. Agents can never modify their own permissions.")
    p = db.get(Policy, policy_id)
    if not p or p.organization_id != principal.organization_id:
        if p:
            audit.record(db, "ACCESS_DENIED", principal, decision="DENIED",
                         reason=f"{principal.name} attempted to modify policy {policy_id} of another organization", commit=True)
        raise HTTPException(404, "Policy not found")
    before = policy_snapshot(p)
    data = body.model_dump(exclude_none=True)
    json_fields = {"allowed_categories": "allowed_categories_json", "blocked_categories": "blocked_categories_json",
                   "blocked_products": "blocked_products_json"}
    for k, v in data.items():
        if k == "clear_max_quantity":
            if v:
                p.max_quantity = None
            continue
        if k in json_fields:
            setattr(p, json_fields[k], json.dumps([str(x).strip() for x in v if str(x).strip()]))
        else:
            setattr(p, k, v)
    if sum([p.weight_price, p.weight_reliability, p.weight_sustainability, p.weight_match]) <= 0:
        raise HTTPException(422, "Ranking weights must not all be zero")
    p.version += 1
    p.updated_at = utcnow()
    p.updated_by = principal.name
    after = policy_snapshot(p)
    changed = {k: {"from": before[k], "to": after[k]} for k in after if before.get(k) != after[k] and k not in ("version", "updated_at", "updated_by")}
    audit.record(db, "POLICY_UPDATED", principal, decision=f"v{p.version}",
                 reason=f"Policy '{p.name}' updated to version {p.version}", payload={"changes": changed}, commit=True)
    return after


# ------------------------------------------------------------------ audit
@router.get("/api/audit", tags=["Audit"])
def list_audit(action: str | None = None, request_id: str | None = None, actor_type: str | None = None,
               decision: str | None = None, q: str | None = Query(None, max_length=200), page: int = Query(1, ge=1),
               page_size: int = Query(50, ge=1, le=200), db: Session = Depends(get_db),
               principal: Principal = Depends(require("view"))):
    query = access.audit_query(db, principal)
    if action:
        query = query.filter(AuditEvent.action == action)
    if request_id:
        query = query.filter(AuditEvent.request_id == request_id)
    if actor_type:
        query = query.filter(AuditEvent.actor_type == actor_type)
    if decision:
        query = query.filter(AuditEvent.decision == decision)
    if q:
        query = query.filter(AuditEvent.reason.ilike(f"%{q}%"))
    total = query.count()
    rows = query.order_by(AuditEvent.ts.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size, "items": [audit.to_dict(e) for e in rows],
            "actions": audit.ACTION_LABELS}


@router.get("/api/audit/{event_id}", tags=["Audit"])
def get_audit(event_id: str, db: Session = Depends(get_db), principal: Principal = Depends(require("view"))):
    return audit.to_dict(access.get_audit_for(db, principal, event_id))


# ------------------------------------------------------------------ analytics & trust
@router.get("/api/analytics", tags=["Analytics"])
def get_analytics(db: Session = Depends(get_db), principal: Principal = Depends(require("view"))):
    return analytics.overview(db, principal)


@router.get("/api/trust", tags=["Trust"])
def get_trust(db: Session = Depends(get_db), principal: Principal = Depends(require("view_trust"))):
    return analytics.trust_overview(db, principal)


@router.post("/api/trust/ping", tags=["Trust"])
def ping(db: Session = Depends(get_db), principal: Principal = Depends(require("view_trust"))):
    result = get_valio_client().ping()
    audit.record(db, "API_HEALTH_CHECK", principal, decision=result["status"], reason=result["detail"], commit=True)
    return analytics.trust_overview(db, principal, ping=result)
