"""Dashboard metrics and Trust Center data, computed from the database (no hardcoded figures)."""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Approval, AuditEvent, Order, ProcurementRequest, Product, Recommendation
from app.services import audit


def overview(db: Session, principal) -> dict:
    """All figures are restricted to the records this principal may see (tenant + read scope)."""
    from app.services import access

    reqs = access.request_query(db, principal).all()
    decisions = Counter(r.decision for r in reqs if r.decision)
    statuses = Counter(r.status for r in reqs)
    proposals = access.order_query(db, principal).filter(Order.kind == "PROPOSAL").all()
    pending = access.approval_query(db, principal).filter(Approval.status == "PENDING").count()
    approvals = access.approval_query(db, principal).filter(Approval.status != "SUPERSEDED").all()
    decided = [a for a in approvals if a.status not in ("PENDING",)]
    approved = [a for a in decided if a.status.startswith("APPROVED")]

    verified_spend = sum(o.total_excl_tax for o in proposals if o.total_excl_tax is not None)
    # Verifiable savings: chosen proposal vs the most expensive VERIFIED eligible alternative for the same request
    savings = 0.0
    savings_n = 0
    rel_vals, carbon_vals, carbon_totals = [], [], []
    for o in proposals:
        line = (json.loads(o.lines_json or "[]") or [{}])[0]
        if line.get("reliability") is not None:
            rel_vals.append(line["reliability"])
        if line.get("carbon_total_kg") is not None:
            carbon_totals.append(line["carbon_total_kg"])
        if o.total_excl_tax is None:
            continue
        recs = db.query(Recommendation).filter(Recommendation.request_id == o.request_id, Recommendation.eligible.is_(True)).all()
        totals = [json.loads(r.data_json).get("total_excl_tax") for r in recs]
        totals = [t for t in totals if t is not None]
        if len(totals) > 1 and max(totals) > o.total_excl_tax:
            savings += max(totals) - o.total_excl_tax
            savings_n += 1
    for r in reqs:
        if r.selected_product_id:
            p = db.get(Product, r.selected_product_id)
            if p and p.carbon_kg_per_kg is not None:
                carbon_vals.append(p.carbon_kg_per_kg)

    # Activity by day
    by_day: dict[str, Counter] = defaultdict(Counter)
    for r in reqs:
        day = r.created_at.date().isoformat() if r.created_at else "unknown"
        by_day[day]["requests"] += 1
        by_day[day][r.decision or "PENDING"] += 1
    activity = [{"day": d, "requests": c["requests"], "allowed": c["ALLOWED"], "approval": c["REQUIRES_APPROVAL"],
                 "blocked": c["BLOCKED"]} for d, c in sorted(by_day.items())][-14:]
    recent = access.audit_query(db, principal).order_by(AuditEvent.ts.desc()).limit(12).all()
    total_products = db.query(func.count(Product.id)).scalar() or 0
    return {
        "total_requests": len(reqs),
        "autonomous_decisions": decisions.get("ALLOWED", 0),
        "requires_approval": decisions.get("REQUIRES_APPROVAL", 0),
        "blocked_decisions": decisions.get("BLOCKED", 0),
        "pending_approvals": pending,
        "proposals_ready": len(proposals),
        "status_breakdown": dict(statuses),
        "decision_breakdown": dict(decisions),
        "approval_rate": round(len(approved) / len(decided), 3) if decided else None,
        "approvals_decided": len(decided),
        "verified_spend_excl_tax": round(verified_spend, 2),
        "proposals_with_verified_total": sum(1 for o in proposals if o.total_excl_tax is not None),
        "verifiable_savings": round(savings, 2) if savings_n else None,
        "savings_basis": f"Selected vs most expensive verified eligible option, {savings_n} proposal(s)" if savings_n
        else "No proposals with more than one verified price yet",
        "avg_reliability_selected": round(sum(rel_vals) / len(rel_vals), 4) if rel_vals else None,
        "avg_carbon_selected_kg_per_kg": round(sum(carbon_vals) / len(carbon_vals), 3) if carbon_vals else None,
        "carbon_total_proposals_kg": round(sum(carbon_totals), 1) if carbon_totals else None,
        "catalogue": {
            "products": total_products,
            "with_reliability": db.query(func.count(Product.id)).filter(Product.reliability.isnot(None)).scalar() or 0,
            "with_carbon": db.query(func.count(Product.id)).filter(Product.carbon_kg_per_kg.isnot(None)).scalar() or 0,
            "allergen_declared": db.query(func.count(Product.id)).filter(Product.allergen_data_status == "DECLARED").scalar() or 0,
            "avg_reliability": round(db.query(func.avg(Product.reliability)).scalar() or 0, 4) or None,
            "avg_carbon": round(db.query(func.avg(Product.carbon_kg_per_kg)).scalar() or 0, 3) or None,
        },
        "activity": activity,
        "recent_activity": [audit.to_dict(e) for e in recent],
        "live_api_configured": get_settings().live_api_configured,
        "scope": {"organization_id": principal.organization_id, "read_scope": principal.read_scope,
                  "label": ("Your organization" if principal.read_scope == "ORG" and principal.kind == "USER"
                            else "Your own requests")},
    }


def trust_overview(db: Session, principal, ping: dict | None = None) -> dict:
    from app.services import access, integration_status

    s = get_settings()
    total = db.query(func.count(Product.id)).scalar() or 0
    rel = db.query(func.count(Product.id)).filter(Product.reliability.isnot(None)).scalar() or 0
    car = db.query(func.count(Product.id)).filter(Product.carbon_kg_per_kg.isnot(None)).scalar() or 0
    alg = db.query(func.count(Product.id)).filter(Product.allergen_data_status == "DECLARED").scalar() or 0
    synk = db.query(func.count(Product.id)).filter(Product.synkka_complete.is_(True)).scalar() or 0
    subs = db.query(func.count(Product.id)).filter(Product.substitutions_json != "[]").scalar() or 0

    # Verification states of evidence across recent recommendations
    states = Counter()
    by_claim: dict[str, Counter] = defaultdict(Counter)
    unknown_examples = []
    recs = (db.query(Recommendation).filter(Recommendation.eligible.is_(True),
                                            Recommendation.request_id.in_(access.visible_request_ids(db, principal)))
            .order_by(Recommendation.id.desc()).limit(300).all())
    for r in recs:
        for e in json.loads(r.data_json).get("evidence", []):
            states[e["state"]] += 1
            key = e["claim"].split("_")[0] if e["claim"].startswith(("allergen_", "claim_")) else e["claim"]
            by_claim[key][e["state"]] += 1
            if e["state"] != "VERIFIED" and len(unknown_examples) < 15:
                unknown_examples.append({**e, "request_id": r.request_id})
    integ = integration_status.summary(db)
    api_stats = {}
    for op in integ["operations"].values():
        for k, v in op["counts"].items():
            api_stats[k] = api_stats.get(k, 0) + v
    return {
        "verification_model": {
            "VERIFIED": "Directly confirmed by an authoritative source (Valio API response or official dataset field)",
            "UNKNOWN": "Insufficient information to verify (missing field, placeholder value, name-only evidence)",
            "ERROR": "Source failed or returned an error (timeout, HTTP error, malformed response, no API key)",
        },
        "api": {
            "base_url": s.valio_api_base_url,
            "configured": s.live_api_configured,
            "mode": "LIVE (key configured)" if s.live_api_configured else "DEMO — LIVE API UNAVAILABLE",
            "connectivity": integ["connectivity"],
            "operations": integ["operations"],
            "simulated_events": integ["simulated_events"],
            "endpoints": ["GET /api/ping", "POST /api/price", "POST /api/availability"],
            "order_endpoint": None,
            "ping": ping,
            "recent_calls": dict(api_stats),
            "auth": "X-API-Key header, server-side only",
        },
        "llm": {"provider": s.llm_provider, "configured": s.llm_configured,
                "role": "Intent extraction only; validated and sanitised; cannot decide or approve"},
        "sources": [
            {"name": "Product catalogue", "file": "valio_aimo_junction26_product_data.json", "records": total,
             "validation": "JSON Schema draft 2020-12 (official schema)", "granularity": "Product-specific",
             "coverage": 1.0 if total else 0},
            {"name": "Manufacturer data (GS1 Synkka)", "file": "synkkaData", "records": synk,
             "granularity": "Product-specific", "coverage": round(synk / total, 3) if total else 0},
            {"name": "Allergen declarations", "file": "synkkaData.classifications", "records": alg,
             "granularity": "Product-specific", "coverage": round(alg / total, 3) if total else 0},
            {"name": "Delivery reliability", "file": "valio_aimo_junction26_delivery_reliability.csv", "records": rel,
             "granularity": "Product-specific, historical", "coverage": round(rel / total, 3) if total else 0},
            {"name": "Carbon footprint", "file": "valio_aimo_junction26_carbon_footprint.csv", "records": car,
             "granularity": "Product-group average (kg CO2e per kg) — category-level estimate", "coverage": round(car / total, 3) if total else 0},
            {"name": "Substitution rules", "file": "product.substitutions", "records": subs,
             "granularity": "Product-specific", "coverage": round(subs / total, 3) if total else 0},
            {"name": "Price", "file": "Valio API POST /api/price", "records": None, "granularity": "Per request, per date",
             "coverage": None},
            {"name": "Availability", "file": "Valio API POST /api/availability", "records": None,
             "granularity": "Per request, per date", "coverage": None},
        ],
        "evidence_states": dict(states),
        "evidence_by_claim": {k: dict(v) for k, v in by_claim.items()},
        "unverified_examples": unknown_examples,
    }
