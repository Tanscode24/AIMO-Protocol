"""AIMO procurement agent pipeline.

parse -> search -> static hard constraints (claims, allergens, units, category) -> shortlist
-> live price + availability (Valio API, shortlist only) -> availability hard constraints
-> dataset substitutions -> deterministic ranking -> policy engine per candidate
-> decision on the top candidate: proposal | approval request | blocked.

Every step writes an audit event. No supplier order is ever placed: the Valio API has no
order endpoint, so the end state is "PROPOSAL READY — NOT SUBMITTED TO SUPPLIER".
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.context import get_correlation_id
from app.integrations.valio_client import CallResult, ValioClient, get_valio_client
from app.models import Approval, IdempotencyRecord, Order, Policy, ProcurementRequest, Product, Recommendation, new_id, utcnow
from app.schemas import ProcurementIntent
from app.security import DEFAULT_AGENT_ID, Principal
from app.services import audit
from app.services.allergens import evaluate_claim, evaluate_exclusions, product_allergen_summary
from app.services.parser import parse_request
from app.services.policy import BLOCKED, REQUIRES_APPROVAL, DecisionContext, PolicyResult, evaluate, policy_snapshot
from app.services.comparison import build_comparison
from app.services.ranking import explain, explain_pair, rank, weights_from_policy
from app.services.search import fts_candidate_ids, match_score
from app.services.units import convert_request, is_valid_lot, net_weight_kg_per_sales_unit
from app.services.verification import fmt_qty, evidence_item, interpret_availability, interpret_price, parse_date

MATCH_THRESHOLD = 0.5
MAX_STATIC_EVAL = 80
DECISION_RANK = {"ALLOWED": 0, "REQUIRES_APPROVAL": 1, "BLOCKED": 2}
ACTIVE_STATUSES = ("PENDING_APPROVAL", "PROPOSAL_READY")
PROPOSAL_STATUS = "PROPOSAL_READY_NOT_SUBMITTED"
DISCLAIMER = ("PROPOSAL READY — NOT SUBMITTED TO SUPPLIER. The Valio Aimo API exposes no order endpoint; "
              "AIMO never places or simulates supplier orders.")


# ------------------------------------------------------------------ helpers
def active_policy(db: Session, organization_id: str | None) -> Policy:
    p = (db.query(Policy).filter(Policy.active.is_(True), Policy.organization_id == organization_id)
         .order_by(Policy.updated_at.desc()).first())
    if not p:
        raise HTTPException(500, "No active procurement policy configured")
    return p


def fingerprint(intent: ProcurementIntent) -> str:
    key = "|".join([
        intent.product_query.lower().strip(), intent.display_query.lower().strip(),
        f"{intent.quantity or ''}", intent.unit or "", intent.delivery_date.isoformat() if intent.delivery_date else "",
        ",".join(sorted(intent.excluded_allergens)), ",".join(sorted(intent.required_attributes)),
    ])
    return hashlib.sha256(key.encode()).hexdigest()[:40]


def _j(v: Any) -> str:
    return json.dumps(v, default=str)


def static_evaluate(product: Product, intent: ProcurementIntent, policy: dict, match: dict) -> dict:
    """Hard constraints that need no live data."""
    claims = {c: evaluate_claim(product, c) for c in intent.required_attributes}
    allergens = evaluate_exclusions(product, intent.excluded_allergens)
    conversion = convert_request(product, intent.quantity, intent.unit)
    exclusions: list[str] = []
    if any(c["status"] == "NOT_MET" for c in claims.values()):
        exclusions += [f"Requirement not met: {c['claim']} ({c['detail']})" for c in claims.values() if c["status"] == "NOT_MET"]
    if allergens["overall"] == "VIOLATION":
        bad = [f"{c['code']} {c['label']} {c['status']}" for c in allergens["checks"] if c["status"] in ("CONTAINS", "MAY_CONTAIN")]
        exclusions.append("Excluded allergen present: " + ", ".join(bad))
    if allergens["overall"] == "UNKNOWN" and policy["unknown_allergen_action"] == "BLOCK":
        exclusions.append("Allergen safety cannot be verified and policy blocks unknown allergen data")
    if conversion["status"] in ("CONFLICT", "UNKNOWN"):
        exclusions.append(f"Unit conversion {conversion['status'].lower()}: " + "; ".join(conversion.get("notes", [])[-2:]))
    if product.id in policy["blocked_products"]:
        exclusions.append("Product blocked by policy")
    if product.category in policy["blocked_categories"]:
        exclusions.append(f"Category {product.category} blocked by policy")
    if policy["allowed_categories"] and product.category not in policy["allowed_categories"]:
        exclusions.append(f"Category {product.category} not in allowed categories")
    verified_static = (
        allergens["overall"] in ("NOT_REQUIRED", "VERIFIED_SAFE")
        and all(c["status"] == "VERIFIED" for c in claims.values())
        and conversion["status"] == "VERIFIED"
    )
    return {"claims": claims, "allergens": allergens, "conversion": conversion, "exclusions": exclusions,
            "verified_static": verified_static, "match": match}


def build_candidate(product: Product, intent: ProcurementIntent, policy: dict, static: dict,
                    price_call: CallResult | None, avail_call: CallResult | None,
                    source: str = "CATALOGUE_SEARCH", substitute_for: str | None = None) -> dict:
    d = intent.delivery_date or date.today()
    qty = static["conversion"].get("quantity_sales_units")
    price = interpret_price(price_call, product.id, d) if price_call else None
    avail = interpret_availability(avail_call, product.id, d, qty, product.sales_unit) if avail_call else None
    total_excl = total_incl = None
    if price and price["status"] == "VERIFIED" and qty is not None:
        total_excl = round(price["unit_price_excl_tax"] * qty, 2)
        if price.get("unit_price_incl_tax") is not None:
            total_incl = round(price["unit_price_incl_tax"] * qty, 2)
    weight = net_weight_kg_per_sales_unit(product)
    carbon_total = None
    if product.carbon_kg_per_kg is not None and qty is not None and weight["status"] == "VERIFIED" and weight["value"]:
        carbon_total = round(product.carbon_kg_per_kg * weight["value"] * qty, 2)
    now = datetime.now(timezone.utc).isoformat()
    ev = []
    pid = product.id
    ev.append(evidence_item(f"ev:{pid}:catalogue", "catalogue_record", product.display_name, "VERIFIED",
                            "valio_aimo_junction26_product_data.json", pid, product.synkka_last_updated,
                            f"Schema-validated record; completeness {product.completeness * 100:.0f}%"))
    conv = static["conversion"]
    ev.append(evidence_item(f"ev:{pid}:conversion", "quantity_conversion",
                            f"{qty:g} {product.sales_unit}" if qty is not None else None,
                            "VERIFIED" if conv["status"] == "VERIFIED" else ("UNKNOWN" if conv["status"] == "ESTIMATED" else "ERROR"),
                            (conv.get("per_sales_unit") or {}).get("source") or "salesUnit / units / allowedLotSize",
                            pid, now, "; ".join(conv.get("notes", []))))
    for c in static["allergens"]["checks"]:
        ev.append(evidence_item(f"ev:{pid}:allergen:{c['code']}", f"allergen_{c['code']}", c["status"],
                                "VERIFIED" if c["verified"] else "UNKNOWN", "synkkaData.classifications", pid,
                                product.synkka_last_updated, c["detail"]))
    for c in static["claims"].values():
        ev.append(evidence_item(f"ev:{pid}:claim:{c['claim']}", f"claim_{c['claim']}", c["status"],
                                "VERIFIED" if c["status"] == "VERIFIED" else "UNKNOWN", "synkkaData.classifications.nutritionalClaim",
                                pid, product.synkka_last_updated, c["detail"]))
    if price:
        pdetail = price.get("reason") or ""
        if price.get("status") == "VERIFIED":
            pdetail += (f"; per {product.sales_unit} excl. VAT, VAT {price.get('tax_rate')}%, "
                        f"valid {str(price.get('valid_from'))[:10]} → {str(price.get('valid_to'))[:10]}, pricing date {price.get('pricing_date')}")
        ev.append(evidence_item(f"ev:{pid}:price", "price", price.get("unit_price_excl_tax"), price["trust"],
                                price["source"], pid, price["fetched_at"], pdetail))
    if avail:
        sel = avail.get("selected_line")
        adetail = avail.get("reason") or ""
        if sel:
            basis = {"TERMINAL_PLACEHOLDER": " (Terminal placeholder, quantity unknown)",
                     "UNCONFIRMED_STOCK_SENTINEL": " (Stock sentinel pending Valio confirmation, quantity NOT confirmed)"}
            adetail += (f"; selected schedule line #{sel['index']}: raw availableAmount={fmt_qty(sel['available_amount'])} "
                        f"{sel['sales_unit']} on {sel['parsed_date']}{basis.get(sel.get('quantity_basis'), '')}")
        if avail.get("lines_total", 0) > 1:
            adetail += f"; {avail['lines_total']} lines evaluated: " + ", ".join(
                f"#{ln['index']} {ln['classification']}" for ln in avail.get("lines", []))
        ev.append(evidence_item(f"ev:{pid}:availability", "availability", avail["status"], avail["trust"],
                                avail["source"], pid, avail["fetched_at"], adetail))
    ev.append(evidence_item(f"ev:{pid}:reliability", "delivery_reliability", product.reliability,
                            "VERIFIED" if product.reliability is not None else "UNKNOWN",
                            "valio_aimo_junction26_delivery_reliability.csv", pid, None,
                            "Historical, product-specific delivery performance — not a delivery guarantee" if product.reliability is not None else "No reliability record for this product — UNKNOWN, not zero"))
    ev.append(evidence_item(f"ev:{pid}:carbon", "carbon_kg_co2e_per_kg", product.carbon_kg_per_kg,
                            "VERIFIED" if product.carbon_kg_per_kg is not None else "UNKNOWN",
                            "valio_aimo_junction26_carbon_footprint.csv", pid, None,
                            "Product-group average estimate, not a product-specific measurement" if product.carbon_kg_per_kg is not None
                            else "No carbon record for this product — UNKNOWN, not zero"))

    # ---- transparency blocks (each claim carries source, state, timestamp, product id, evidence ref)
    conv_per = conv.get("per_sales_unit") or {}
    size = conv.get("sales_unit_size_in_base_units")
    pack_parts = [f"1 {product.sales_unit}"]
    if size and product.sales_unit != product.base_unit:
        pack_parts.append(f"= {size:g} × {product.base_unit}")
    if conv_per.get("value"):
        pack_parts.append(f"= {conv_per['value']:g} {conv_per.get('unit')} ({conv_per.get('status')})")
    packaging = {
        "sales_unit": product.sales_unit, "base_unit": product.base_unit, "sales_unit_size_in_base_units": size,
        "per_sales_unit_amount": conv_per.get("value"), "per_sales_unit_unit": conv_per.get("unit"),
        "per_sales_unit_status": conv_per.get("status"), "per_sales_unit_source": conv_per.get("source"),
        "allowed_lot_size": product.allowed_lot_size, "description": " ".join(pack_parts),
        "evidence_ref": f"ev:{pid}:conversion",
    }
    req_unit = {"L": "L", "KG": "kg", "SALES_UNIT": product.sales_unit}.get(intent.unit or "", intent.unit or None)
    deliverable = conv.get("delivered_amount")
    if deliverable is None and intent.unit in (None, "SALES_UNIT"):
        deliverable = qty
    quantity = {
        "requested_amount": intent.quantity, "requested_unit": req_unit,
        "order_quantity": qty, "sales_unit": product.sales_unit,
        "deliverable_amount": deliverable,
        "deliverable_unit": conv_per.get("unit") if conv.get("delivered_amount") is not None else product.sales_unit,
        "surplus": round(deliverable - intent.quantity, 4) if deliverable is not None and intent.quantity else None,
        "lot_size": product.allowed_lot_size, "status": conv["status"], "notes": conv.get("notes", []),
        "evidence_ref": f"ev:{pid}:conversion",
    }
    if price:
        price = {**price, "vat_rate": price.get("tax_rate"), "total_excl_tax": total_excl, "total_incl_tax": total_incl,
                 "total_incl_tax_basis": "valueWithTax × quantity (Valio API)" if total_incl is not None else None,
                 "evidence_ref": f"ev:{pid}:price"}
    if avail:
        ad = parse_date(avail.get("available_date"))
        avail = {**avail, "on_time": (ad <= d) if ad else None, "evidence_ref": f"ev:{pid}:availability"}
    reliability_info = {
        "value": product.reliability,
        "basis": "HISTORICAL_DELIVERY_PERFORMANCE",
        "is_guarantee": False,
        "product_specific": True if product.reliability is not None else None,
        "state": "VERIFIED" if product.reliability is not None else "UNKNOWN",
        "source": "valio_aimo_junction26_delivery_reliability.csv",
        "label": ("Historical delivery reliability — past performance, not a guaranteed delivery"
                  if product.reliability is not None else "UNKNOWN — no reliability record (not zero)"),
        "evidence_ref": f"ev:{pid}:reliability",
    }
    carbon_info = {
        "value_per_kg": product.carbon_kg_per_kg, "unit": "kg CO2e per kg product",
        "basis": "PRODUCT_GROUP_AVERAGE", "product_specific": False,
        "state": "VERIFIED" if product.carbon_kg_per_kg is not None else "UNKNOWN",
        "source": "valio_aimo_junction26_carbon_footprint.csv (column 'AVG kg CO2-eq/kg')",
        "label": ("Estimated product-group average, not a measurement of this specific product"
                  if product.carbon_kg_per_kg is not None else "UNKNOWN — no carbon record (not zero)"),
        "order_total_kg": carbon_total,
        "order_total_status": "COMPUTED_FROM_VERIFIED_NET_WEIGHT" if carbon_total is not None else "UNKNOWN",
        "order_total_reason": None if carbon_total is not None else (
            "No carbon record" if product.carbon_kg_per_kg is None else
            f"Net weight per {product.sales_unit} not verified ({weight['status']})" if qty is not None else "Order quantity unknown"),
        "evidence_ref": f"ev:{pid}:carbon",
    }

    exclusions = list(static["exclusions"])
    if avail:
        if avail["status"] in ("UNAVAILABLE", "PARTIAL"):
            exclusions.append(f"Availability {avail['status']}: {avail['reason']}")
        elif avail["status"] == "LATE" and not policy["allow_late_delivery"]:
            exclusions.append(f"Late delivery: {avail['reason']}")

    missing = []
    if not price or price["status"] != "VERIFIED":
        missing.append("verified price")
    if not avail or avail["status"] not in ("AVAILABLE",):
        missing.append("confirmed availability")
    if product.reliability is None:
        missing.append("delivery reliability")
    if product.carbon_kg_per_kg is None:
        missing.append("carbon estimate")
    if product.allergen_data_status == "UNKNOWN":
        missing.append("allergen data")
    if carbon_total is None and product.carbon_kg_per_kg is not None:
        missing.append("verified net weight (order carbon total)")

    return {
        "id": pid,
        "product": {
            "id": pid, "name": product.display_name, "brand": product.brand, "vendor_name": product.vendor_name,
            "category": product.category, "sales_unit": product.sales_unit, "base_unit": product.base_unit,
            "allowed_lot_size": product.allowed_lot_size, "units": json.loads(product.units_json or "[]"),
            "key_ingredients": product.key_ingredients, "completeness": product.completeness,
            "allergen_summary": product_allergen_summary(product),
        },
        "source": source,
        "substitute_for": substitute_for,
        "match": static["match"],
        "claims": list(static["claims"].values()),
        "allergens": static["allergens"],
        "conversion": static["conversion"],
        "price": price,
        "availability": avail,
        "quantity_sales_units": qty,
        "total_excl_tax": total_excl,
        "total_incl_tax": total_incl,
        "currency": (price or {}).get("currency"),
        "reliability": product.reliability,
        "carbon_kg_per_kg": product.carbon_kg_per_kg,
        "carbon_total_kg": carbon_total,
        "net_weight_per_sales_unit": weight,
        "packaging": packaging,
        "quantity": quantity,
        "reliability_info": reliability_info,
        "carbon_info": carbon_info,
        "exclusions": exclusions,
        "eligible": not exclusions,
        "verified_static": static["verified_static"],
        "missing": missing,
        "evidence": ev,
        "metrics": {
            "price": total_excl,
            "reliability": product.reliability,
            "sustainability": product.carbon_kg_per_kg,
            "match": static["match"]["score"],
        },
    }


def _unconfirmed(req: ProcurementRequest, intent: ProcurementIntent) -> list[str]:
    """Material assumptions (quantity, unit, date) that the requester has not confirmed."""
    if req.confirmed_assumptions:
        return []
    return list(intent.confirmation_required)


def policy_for_candidate(policy: dict, cand: dict, intent: ProcurementIntent, duplicate_of: str | None = None,
                         unconfirmed: list[str] | None = None) -> PolicyResult:
    qty = cand["quantity_sales_units"]
    ctx = DecisionContext(
        product_id=cand["id"],
        category=cand["product"]["category"],
        quantity_sales_units=qty,
        conversion_status=cand["conversion"]["status"],
        price_status=(cand["price"] or {}).get("status", "API_ERROR"),
        total_excl_tax=cand["total_excl_tax"],
        availability_status=(cand["availability"] or {}).get("status", "API_ERROR"),
        cutoff_passed=bool((cand["availability"] or {}).get("cutoff_passed")),
        allergen_overall=cand["allergens"]["overall"],
        claim_statuses={c["claim"]: c["status"] for c in cand["claims"]},
        is_substitute=cand["source"] == "DATASET_SUBSTITUTION",
        budget_limit=intent.budget_limit,
        duplicate_of=duplicate_of,
        lot_valid=qty is None or is_valid_lot(qty, cand["product"]["allowed_lot_size"]),
        unconfirmed_assumptions=unconfirmed or [],
    )
    return evaluate(policy, ctx)


def _tier(c: dict) -> str:
    fully = (
        c["verified_static"]
        and (c["price"] or {}).get("status") == "VERIFIED"
        and (c["availability"] or {}).get("status") == "AVAILABLE"
    )
    return "FULLY_VERIFIED" if fully else "NEEDS_VERIFICATION"


# ------------------------------------------------------------------ pipeline
def run_procurement(
    db: Session,
    text: str,
    principal: Principal,
    *,
    simulate: str | None = None,
    auto_decide: bool = True,
    intent_override: ProcurementIntent | None = None,
    channel: str = "WORKSPACE",
    idempotency_key: str | None = None,
    client: ValioClient | None = None,
    confirm_assumptions: bool = False,
) -> ProcurementRequest:
    settings = get_settings()
    client = client or get_valio_client()
    if idempotency_key:
        existing = db.query(ProcurementRequest).filter(ProcurementRequest.idempotency_key == idempotency_key,
                                                       ProcurementRequest.organization_id == principal.organization_id).first()
        if existing:
            return existing
    pol = active_policy(db, principal.organization_id)
    snap = policy_snapshot(pol)
    req = ProcurementRequest(
        organization_id=principal.organization_id, correlation_id=get_correlation_id(),
        confirmed_assumptions=confirm_assumptions,
        overrides_json=json.dumps({"intent_override": intent_override is not None, "confirm_assumptions": confirm_assumptions}),
        raw_text=text.strip(), created_by=principal.id if principal.kind == "USER" else None,
        created_by_name=principal.name, agent_id=principal.id if principal.kind == "AGENT" else DEFAULT_AGENT_ID,
        channel=channel, fingerprint="pending", status="ANALYZING", policy_id=pol.id, policy_version=pol.version,
        simulate=simulate, idempotency_key=idempotency_key, live_api_used=client.configured and not simulate,
    )
    db.add(req)
    db.flush()
    audit.record(db, "REQUEST_RECEIVED", principal, request_id=req.id, reason=f"Channel {channel}", policy_version=pol.version,
                 payload={"text": req.raw_text, "simulate": simulate, "policy": f"{pol.name} v{pol.version}"})

    # 1. Intent
    if intent_override:
        intent, parser_source, llm_err = intent_override, "PROVIDED", None
    else:
        intent, parser_source, llm_err = parse_request(text)
    req.parsed_json = intent.model_dump_json()
    req.parser_source = parser_source
    req.fingerprint = fingerprint(intent)
    req.delivery_date = intent.delivery_date.isoformat() if intent.delivery_date else None
    audit.record(db, "INTENT_EXTRACTED", principal, request_id=req.id,
                 reason=f"Parser {parser_source}" + (f" — {llm_err}" if llm_err else ""),
                 payload=json.loads(req.parsed_json))

    # 2. Catalogue search + match scoring
    ids = fts_candidate_ids(db, intent.product_query)
    products = {p.id: p for p in db.query(Product).filter(Product.id.in_(ids)).all()} if ids else {}
    all_scored = []
    for pid in ids:
        p = products.get(pid)
        if p:
            all_scored.append((p, match_score(p, intent.product_query)))
    # Strict mode: when enough products match every word of the request (e.g. "unsalted butter"),
    # partial matches ("concentrated butter") are not considered.
    full = [t for t in all_scored if t[1]["score"] >= 0.99]
    strict = len(full) >= 3
    threshold = 0.99 if strict else MATCH_THRESHOLD
    scored = [t for t in all_scored if t[1]["score"] >= threshold]
    scored.sort(key=lambda t: (-t[1]["score"], -t[0].completeness))
    audit.record(db, "CATALOGUE_SEARCHED", principal, request_id=req.id,
                 reason=f"'{intent.product_query}': {len(ids)} text hits, {len(scored)} matched the product type"
                        f" ({'strict: all words' if strict else f'match >= {MATCH_THRESHOLD:.0%}'})",
                 evidence=[{"source": "products_fts (SQLite FTS5)", "query": intent.product_query}],
                 payload={"top_matches": [{"id": p.id, "name": p.display_name, "match": m["score"]} for p, m in scored[:10]]})

    # 3. Static hard constraints
    statics = [(p, static_evaluate(p, intent, snap, m)) for p, m in scored[:MAX_STATIC_EVAL]]
    passing = [(p, s) for p, s in statics if not s["exclusions"]]
    static_excluded = [(p, s) for p, s in statics if s["exclusions"]]
    passing.sort(key=lambda t: (not t[1]["verified_static"], -t[1]["match"]["score"],
                                -(t[0].reliability or 0), -t[0].completeness))
    shortlist = passing[: settings.valio_max_shortlist]
    conv_counts: dict[str, int] = {}
    for _, s in statics:
        conv_counts[s["conversion"]["status"]] = conv_counts.get(s["conversion"]["status"], 0) + 1
    audit.record(db, "UNIT_CONVERSION_CHECKED", principal, request_id=req.id,
                 reason=f"Conversion states across {len(statics)} matches: {conv_counts}",
                 payload={"requested": {"quantity": intent.quantity, "unit": intent.unit}})
    audit.record(db, "PRODUCTS_SHORTLISTED", principal, request_id=req.id,
                 reason=f"{len(passing)} passed static hard constraints, {len(static_excluded)} excluded; "
                        f"{len(shortlist)} shortlisted for live verification",
                 payload={"shortlist": [p.id for p, _ in shortlist],
                          "excluded_examples": [{"id": p.id, "why": s["exclusions"][0]} for p, s in static_excluded[:8]]})

    # 4. Live price + availability for the shortlist only
    day = intent.delivery_date or date.today()
    sl_ids = [p.id for p, _ in shortlist]
    price_call = client.get_prices(sl_ids, day, simulate) if sl_ids else None
    avail_call = client.get_availability(sl_ids, day, simulate) if sl_ids else None
    if price_call:
        audit.record(db, "PRICES_RETRIEVED", principal, request_id=req.id, decision=price_call.status,
                     reason=price_call.error or f"{len(price_call.items)} prices returned ({price_call.from_cache} from cache)",
                     evidence=[{"source": price_call.source, "fetched_at": price_call.fetched_at, "live": price_call.live,
                                "simulated": price_call.simulated}],
                     payload={"products": sl_ids, "requestedDate": day.isoformat()})
        audit.record(db, "AVAILABILITY_RETRIEVED", principal, request_id=req.id, decision=avail_call.status,
                     reason=avail_call.error or f"{len(avail_call.items)} availability lines returned",
                     evidence=[{"source": avail_call.source, "fetched_at": avail_call.fetched_at, "live": avail_call.live,
                                "simulated": avail_call.simulated}],
                     payload={"products": sl_ids, "requestedDate": day.isoformat()})

    candidates = [build_candidate(p, intent, snap, s, price_call, avail_call) for p, s in shortlist]

    # 5. Dataset substitutions for products failing on availability
    known = {c["id"] for c in candidates}
    sub_pairs: list[tuple[str, str]] = []
    for c in candidates:
        if c["availability"] and c["availability"]["status"] in ("UNAVAILABLE", "PARTIAL", "LATE") and not c["eligible"]:
            prod = products.get(c["id"]) or db.get(Product, c["id"])
            for sid in json.loads(prod.substitutions_json or "[]"):
                if sid not in known:
                    sub_pairs.append((sid, c["id"]))
                    known.add(sid)
    sub_candidates = []
    if sub_pairs:
        sub_products = {p.id: p for p in db.query(Product).filter(Product.id.in_([s for s, _ in sub_pairs])).all()}
        sub_ids = list(sub_products)
        sp = client.get_prices(sub_ids, day, simulate) if sub_ids else None
        sa = client.get_availability(sub_ids, day, simulate) if sub_ids else None
        for sid, orig in sub_pairs:
            sprod = sub_products.get(sid)
            if not sprod:
                continue
            st = static_evaluate(sprod, intent, snap, match_score(sprod, intent.product_query))
            sub_candidates.append(build_candidate(sprod, intent, snap, st, sp, sa, "DATASET_SUBSTITUTION", orig))
        audit.record(db, "SUBSTITUTIONS_EVALUATED", principal, request_id=req.id,
                     reason=f"{len(sub_candidates)} dataset substitution(s) evaluated, "
                            f"{sum(1 for c in sub_candidates if c['eligible'])} eligible",
                     payload={"pairs": [{"substitute": s, "for": o} for s, o in sub_pairs]})
    candidates += sub_candidates

    # 6. Rank eligible candidates, then evaluate policy for each
    weights = weights_from_policy(pol, intent.priorities)
    eligible = [c for c in candidates if c["eligible"]]
    ranked = rank(eligible, weights)
    for c in ranked:
        c["tier"] = _tier(c)
        pr = policy_for_candidate(snap, c, intent, unconfirmed=_unconfirmed(req, intent))
        c["policy"] = pr.to_dict()
        c["reasons"], c["tradeoffs"] = explain(c, ranked)
    ranked.sort(key=lambda c: (DECISION_RANK[c["policy"]["decision"]], c["tier"] != "FULLY_VERIFIED",
                               c["source"] == "DATASET_SUBSTITUTION", -c["score"]))
    labels = ["BEST", "BETTER", "GOOD"]
    li = 0
    for i, c in enumerate(ranked, start=1):
        c["rank"] = i
        c["label"] = None
        if li < 3 and c["policy"]["decision"] != BLOCKED and len(c["factor_scores"]) >= 2:
            c["label"] = labels[li]
            li += 1
    top = ranked[0] if ranked else None
    audit.record(db, "RECOMMENDATION_GENERATED", principal, request_id=req.id, product_id=top["id"] if top else None,
                 decision=top["policy"]["decision"] if top else None,
                 reason=(f"Top: {top['product']['name']} (score {top['score']:.2f}, {top['tier']})" if top
                         else "No eligible product satisfies the hard constraints"),
                 evidence=[e["id"] for e in (top["evidence"] if top else [])],
                 payload={"weights": weights, "ranking": [{"rank": c["rank"], "id": c["id"], "score": c["score"],
                                                           "policy": c["policy"]["decision"]} for c in ranked[:10]]})

    # 7. Persist recommendations (eligible + excluded with reasons)
    for c in ranked:
        db.add(Recommendation(request_id=req.id, product_id=c["id"], rank=c["rank"], eligible=True, tier=c["tier"],
                              label=c["label"], source=c["source"], substitute_for=c["substitute_for"], score=c["score"],
                              data_json=_j(c)))
    excluded_all = [c for c in candidates if not c["eligible"]]
    for p, s in static_excluded[:25]:
        excluded_all.append({"id": p.id, "product": {"id": p.id, "name": p.display_name, "brand": p.brand,
                                                     "sales_unit": p.sales_unit, "category": p.category},
                             "source": "CATALOGUE_SEARCH", "substitute_for": None, "exclusions": s["exclusions"],
                             "match": s["match"], "conversion": s["conversion"], "allergens": s["allergens"],
                             "claims": list(s["claims"].values()), "eligible": False, "stage": "STATIC"})
    for c in excluded_all:
        c.setdefault("stage", "LIVE")
        db.add(Recommendation(request_id=req.id, product_id=c["id"], eligible=False, source=c["source"],
                              substitute_for=c.get("substitute_for"), data_json=_j(c)))

    req.summary_json = _j({
        "intent": json.loads(req.parsed_json), "weights": weights, "text_hits": len(ids), "matched": len(scored),
        "passed_static": len(passing), "shortlisted": len(shortlist), "eligible": len(ranked),
        "excluded": len(excluded_all), "price_call": price_call.status if price_call else None,
        "availability_call": avail_call.status if avail_call else None,
        "live_api_configured": client.configured, "simulated": simulate,
        "api_error": (price_call.error if price_call and price_call.status != "OK" else None),
    })
    req.status = "ANALYZED"
    db.flush()

    if auto_decide:
        if top:
            decide(db, req, top["id"], principal, client=client, revalidate=False)
        else:
            req.status, req.decision = "BLOCKED", BLOCKED
            req.decision_id = new_id("dec")
            req.decision_reasons_json = _j([{"code": "NO_ELIGIBLE_PRODUCT", "outcome": BLOCKED,
                                              "message": "No catalogue product satisfies all hard constraints"}])
            audit.record(db, "REQUEST_BLOCKED", principal, request_id=req.id, decision=BLOCKED,
                         reason="No eligible product satisfies all hard constraints")
    db.commit()
    return req


def _find_duplicate(db: Session, req: ProcurementRequest, policy: dict) -> str | None:
    window = policy["duplicate_window_minutes"]
    if not window:
        return None
    since = utcnow() - timedelta(minutes=window)
    other = (
        db.query(ProcurementRequest)
        .filter(ProcurementRequest.fingerprint == req.fingerprint, ProcurementRequest.id != req.id,
                ProcurementRequest.organization_id == req.organization_id,
                ProcurementRequest.status.in_(ACTIVE_STATUSES), ProcurementRequest.created_at >= since)
        .order_by(ProcurementRequest.created_at.desc())
        .first()
    )
    return other.id if other else None


def _load_candidate(db: Session, req: ProcurementRequest, product_id: str) -> dict:
    rec = db.query(Recommendation).filter(Recommendation.request_id == req.id, Recommendation.product_id == product_id,
                                          Recommendation.eligible.is_(True)).first()
    if not rec:
        raise HTTPException(400, f"Product {product_id} is not an eligible recommendation for request {req.id}")
    return json.loads(rec.data_json)


def revalidate_candidate(db: Session, req: ProcurementRequest, cand: dict, client: ValioClient) -> dict:
    """Re-run static checks and live price/availability for one product with current data."""
    intent = ProcurementIntent.model_validate_json(req.parsed_json)
    snap = policy_snapshot(active_policy(db, req.organization_id))
    product = db.get(Product, cand["id"])
    static = static_evaluate(product, intent, snap, cand["match"])
    day = intent.delivery_date or date.today()
    pc = client.get_prices([product.id], day, None)
    ac = client.get_availability([product.id], day, None)
    fresh = build_candidate(product, intent, snap, static, pc, ac, cand["source"], cand.get("substitute_for"))
    fresh["tier"] = _tier(fresh)
    return fresh


def decide(db: Session, req: ProcurementRequest, product_id: str | None, principal: Principal, *,
           client: ValioClient | None = None, revalidate: bool = True, idempotency_key: str | None = None) -> ProcurementRequest:
    client = client or get_valio_client()
    if idempotency_key:
        rec = db.get(IdempotencyRecord, idempotency_key)
        if rec and rec.result_ref == req.id:
            return db.get(ProcurementRequest, rec.result_ref)
    if req.status in ("PROPOSAL_READY",):
        raise HTTPException(409, "A procurement proposal already exists for this request")
    pol = active_policy(db, req.organization_id)
    snap = policy_snapshot(pol)
    intent = ProcurementIntent.model_validate_json(req.parsed_json)
    product_id = product_id or req.selected_product_id
    if not product_id:
        top = db.query(Recommendation).filter(Recommendation.request_id == req.id, Recommendation.eligible.is_(True)) \
            .order_by(Recommendation.rank).first()
        if not top:
            raise HTTPException(400, "No eligible recommendation to decide on")
        product_id = top.product_id
    cand = _load_candidate(db, req, product_id)
    if revalidate:
        cand = revalidate_candidate(db, req, cand, client)
        audit.record(db, "REVALIDATION_PERFORMED", principal, request_id=req.id, product_id=product_id,
                     reason=f"Price {(cand['price'] or {}).get('status')}, availability {(cand['availability'] or {}).get('status')}")
    duplicate_of = _find_duplicate(db, req, snap)
    result = policy_for_candidate(snap, cand, intent, duplicate_of, unconfirmed=_unconfirmed(req, intent))
    if not cand["eligible"] and result.decision != BLOCKED:
        result.reasons.append({"code": "HARD_CONSTRAINT", "outcome": BLOCKED, "message": "; ".join(cand["exclusions"])})
        result.decision = BLOCKED

    # Supersede any older pending approval for this request (e.g. buyer picked an alternative)
    for old in db.query(Approval).filter(Approval.request_id == req.id, Approval.status == "PENDING").all():
        old.status, old.new_state, old.previous_state = "SUPERSEDED", "SUPERSEDED", "PENDING"

    req.policy_id, req.policy_version = pol.id, pol.version
    req.decision = result.decision
    req.decision_id = new_id("dec")
    req.decision_reasons_json = _j(result.reasons)
    req.selected_product_id = product_id
    req.selected_quantity = cand["quantity_sales_units"]
    req.estimated_total_excl_tax = cand["total_excl_tax"]
    req.estimated_total_incl_tax = cand["total_incl_tax"]
    req.currency = cand["currency"]
    audit.record(db, "POLICY_EVALUATED", principal, request_id=req.id, product_id=product_id, decision=result.decision,
                 policy_version=pol.version,
                 reason="; ".join(r["message"] for r in result.reasons),
                 evidence=[e["id"] for e in cand["evidence"]],
                 payload={"policy": f"{pol.name} v{pol.version}", "decision_id": req.decision_id, "reasons": result.reasons})

    if result.decision == "ALLOWED":
        create_proposal(db, req, cand, principal, approval=None, policy_decision="ALLOWED")
    elif result.decision == REQUIRES_APPROVAL:
        appr = Approval(request_id=req.id, product_id=product_id, status="PENDING",
                        reason_codes_json=_j(result.approval_codes),
                        reasons_json=_j([r for r in result.reasons if r["outcome"] == REQUIRES_APPROVAL]),
                        snapshot_json=_j(cand), previous_state=None, new_state="PENDING")
        db.add(appr)
        db.flush()
        req.status = "PENDING_APPROVAL"
        audit.record(db, "APPROVAL_REQUESTED", principal, request_id=req.id, product_id=product_id,
                     decision=REQUIRES_APPROVAL, reason="; ".join(r["message"] for r in result.reasons if r["outcome"] == REQUIRES_APPROVAL),
                     payload={"approval_id": appr.id})
    else:
        if duplicate_of:
            req.status = "DUPLICATE"
            audit.record(db, "DUPLICATE_DETECTED", principal, request_id=req.id, product_id=product_id, decision=BLOCKED,
                         reason=f"Same request already active as {duplicate_of}; no second proposal created",
                         payload={"duplicate_of": duplicate_of})
        else:
            req.status = "BLOCKED"
            audit.record(db, "REQUEST_BLOCKED", principal, request_id=req.id, product_id=product_id, decision=BLOCKED,
                         reason="; ".join(r["message"] for r in result.reasons if r["outcome"] == BLOCKED))
    if idempotency_key:
        db.add(IdempotencyRecord(key=idempotency_key, endpoint="decide", result_ref=req.id))
    db.flush()
    db.commit()
    return req


def create_proposal(db: Session, req: ProcurementRequest, cand: dict, principal: Principal, *,
                    approval: Approval | None, policy_decision: str) -> Order:
    existing = db.query(Order).filter(Order.request_id == req.id).first()
    if existing:
        return existing
    price = cand["price"] or {}
    avail = cand["availability"] or {}
    line = {
        "product_id": cand["id"], "name": cand["product"]["name"], "brand": cand["product"]["brand"],
        "quantity": cand["quantity_sales_units"], "sales_unit": cand["product"]["sales_unit"],
        "requested_amount": cand["conversion"].get("requested"), "requested_unit": cand["conversion"].get("requested_unit"),
        "delivered_amount": cand["conversion"].get("delivered_amount"), "conversion_status": cand["conversion"]["status"],
        "unit_price_excl_tax": price.get("unit_price_excl_tax"), "unit_price_incl_tax": price.get("unit_price_incl_tax"),
        "tax_rate": price.get("tax_rate"), "currency": price.get("currency"), "price_status": price.get("status", "API_ERROR"),
        "price_valid_to": price.get("valid_to"),
        "line_total_excl_tax": cand["total_excl_tax"], "line_total_incl_tax": cand["total_incl_tax"],
        "availability_status": avail.get("status", "API_ERROR"), "available_date": avail.get("available_date"),
        "closing_time": avail.get("closing_time"), "logistic_model": avail.get("logistic_model"),
        "is_substitute": cand["source"] == "DATASET_SUBSTITUTION", "substitute_for": cand.get("substitute_for"),
        "carbon_total_kg": cand.get("carbon_total_kg"), "reliability": cand.get("reliability"),
        "price_valid_from": price.get("valid_from"), "pricing_date": price.get("pricing_date"),
        "requested_date": avail.get("requested_date"), "on_time": avail.get("on_time"),
        "packaging": (cand.get("packaging") or {}).get("description"),
        "deliverable_amount": (cand.get("quantity") or {}).get("deliverable_amount"),
        "deliverable_unit": (cand.get("quantity") or {}).get("deliverable_unit"),
        "reliability_basis": "HISTORICAL_DELIVERY_PERFORMANCE (not a guarantee)",
        "carbon_basis": "PRODUCT_GROUP_AVERAGE",
        "carbon_kg_per_kg": cand.get("carbon_kg_per_kg"),
        "evidence_refs": [e["id"] for e in cand.get("evidence", [])],
        "submitted_to_supplier": False,
    }
    order = Order(request_id=req.id, approval_id=approval.id if approval else None, kind="PROPOSAL",
                  status=PROPOSAL_STATUS, submitted_to_supplier=False, policy_decision=policy_decision,
                  approval_status="APPROVED" if approval else "NOT_REQUIRED",
                  total_excl_tax=cand["total_excl_tax"], total_incl_tax=cand["total_incl_tax"],
                  currency=price.get("currency"), delivery_date=req.delivery_date, lines_json=_j([line]),
                  created_by=principal.name, organization_id=req.organization_id)
    db.add(order)
    db.flush()
    req.status = "PROPOSAL_READY"
    audit.record(db, "PROPOSAL_GENERATED", principal, request_id=req.id, product_id=cand["id"], decision=policy_decision,
                 reason=DISCLAIMER, evidence=[e["id"] for e in cand["evidence"]],
                 payload={"proposal_id": order.id, "total_excl_tax": order.total_excl_tax, "submitted_to_supplier": False})
    return order


# ------------------------------------------------------------------ approvals
def act_on_approval(db: Session, approval_id: str, action: str, principal: Principal, reason: str,
                    client: ValioClient | None = None) -> Approval:
    client = client or get_valio_client()
    appr = db.get(Approval, approval_id)
    if not appr:
        raise HTTPException(404, "Approval not found")
    req = db.get(ProcurementRequest, appr.request_id)
    from app.services.access import check_can_decide_approval, get_approval_for

    get_approval_for(db, principal, approval_id)  # tenant + scope visibility (404 + audit if not visible)
    check_can_decide_approval(db, principal, req)  # humans only, separation of duties
    new_status = {"approve": "PROCESSING", "reject": "REJECTED", "request_changes": "CHANGES_REQUESTED"}[action]
    # Single-use: atomic transition out of PENDING
    res = db.execute(
        update(Approval).where(Approval.id == approval_id, Approval.status == "PENDING")
        .values(status=new_status, decided_by=principal.id, decided_by_name=principal.name, decided_at=utcnow(),
                decision_reason=reason or None, previous_state="PENDING", new_state=new_status)
    )
    if res.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "This approval has already been decided and cannot be reused")
    db.flush()
    db.refresh(appr)

    if action == "reject":
        req.status = "REJECTED"
        appr.consumed = True
        audit.record(db, "APPROVAL_REJECTED", principal, request_id=req.id, product_id=appr.product_id,
                     decision="REJECTED", reason=reason or "Rejected by approver",
                     payload={"approval_id": appr.id, "previous_state": "PENDING", "new_state": "REJECTED"})
        db.commit()
        return appr
    if action == "request_changes":
        req.status = "CHANGES_REQUESTED"
        appr.consumed = True
        audit.record(db, "CHANGES_REQUESTED", principal, request_id=req.id, product_id=appr.product_id,
                     decision="CHANGES_REQUESTED", reason=reason or "Changes requested by approver",
                     payload={"approval_id": appr.id, "previous_state": "PENDING", "new_state": "CHANGES_REQUESTED"})
        db.commit()
        return appr

    # Approve: revalidate before finalising
    audit.record(db, "APPROVAL_GRANTED", principal, request_id=req.id, product_id=appr.product_id, decision="APPROVED",
                 reason=reason or "Approved", payload={"approval_id": appr.id, "previous_state": "PENDING"})
    snapshot = json.loads(appr.snapshot_json)
    fresh = revalidate_candidate(db, req, snapshot, client)
    intent = ProcurementIntent.model_validate_json(req.parsed_json)
    snap = policy_snapshot(active_policy(db, req.organization_id))
    result = policy_for_candidate(snap, fresh, intent, unconfirmed=_unconfirmed(req, intent))
    approved_codes = set(json.loads(appr.reason_codes_json))
    new_codes = set(result.approval_codes) - approved_codes
    problems = [r["message"] for r in result.reasons if r["outcome"] == BLOCKED]
    problems += [r["message"] for r in result.reasons if r["code"] in new_codes]
    old_total, new_total = snapshot.get("total_excl_tax"), fresh.get("total_excl_tax")
    if old_total and new_total and abs(new_total - old_total) / old_total > 0.02:
        problems.append(f"Price changed from {old_total:.2f} to {new_total:.2f} since approval was requested")
    if not fresh["eligible"]:
        problems += fresh["exclusions"]
    appr.revalidation_json = _j({"price": fresh["price"], "availability": fresh["availability"],
                                 "decision": result.decision, "problems": problems,
                                 "at": datetime.now(timezone.utc).isoformat()})
    appr.consumed = True
    if problems:
        appr.status = appr.new_state = "APPROVED_REVALIDATION_FAILED"
        req.status = "REVALIDATION_FAILED"
        audit.record(db, "REVALIDATION_FAILED", principal, request_id=req.id, product_id=appr.product_id,
                     decision=BLOCKED, reason="; ".join(problems), payload={"approval_id": appr.id})
    else:
        appr.status = appr.new_state = "APPROVED"
        req.estimated_total_excl_tax, req.estimated_total_incl_tax = fresh["total_excl_tax"], fresh["total_incl_tax"]
        audit.record(db, "REVALIDATION_PERFORMED", principal, request_id=req.id, product_id=appr.product_id,
                     decision="PASSED", reason="No new policy violations since approval was requested")
        create_proposal(db, req, fresh, principal, approval=appr, policy_decision=REQUIRES_APPROVAL)
    db.commit()
    return appr


# ------------------------------------------------------------------ lifecycle
def lifecycle(req: ProcurementRequest, approval: Approval | None, proposal: Order | None) -> list[dict]:
    """Five separate stages. A recommendation is not an order; policy approval is not order placement."""
    has_recs = req.status not in ("ANALYZING",)
    if req.decision == "ALLOWED":
        appr_state, appr_detail = "NOT_REQUIRED", "Within autonomous policy limits; no human approval needed"
    elif approval:
        appr_state = approval.status
        appr_detail = (f"{approval.status.replace('_', ' ').title()}"
                       + (f" by {approval.decided_by_name}" if approval.decided_by_name else " — waiting for an approver"))
    elif req.decision == "BLOCKED":
        appr_state, appr_detail = "NOT_APPLICABLE", "Blocked by policy; nothing to approve"
    else:
        appr_state, appr_detail = "NOT_STARTED", "—"
    return [
        {"stage": "RECOMMENDATION", "state": "DONE" if has_recs else "PENDING",
         "detail": "Ranked options from catalogue + live verification. A recommendation is not an order."},
        {"stage": "POLICY_DECISION", "state": req.decision or "PENDING",
         "detail": "Deterministic backend policy engine (ALLOWED / REQUIRES_APPROVAL / BLOCKED)."},
        {"stage": "HUMAN_APPROVAL", "state": appr_state,
         "detail": appr_detail + ". Approval authorises an internal proposal only; it does not place an order."},
        {"stage": "PROCUREMENT_PROPOSAL", "state": proposal.status if proposal else "NOT_CREATED",
         "detail": "Internal proposal — NOT SUBMITTED TO SUPPLIER." if proposal else
         "Created only after policy (and approval, if required) and successful revalidation."},
        {"stage": "SUPPLIER_ORDER", "state": "NOT_SUPPORTED",
         "detail": "The Valio Aimo API (data/openapi.json) has no order endpoint. AIMO never places or simulates supplier orders."},
    ]


# ------------------------------------------------------------------ backend-confirmed pipeline
PIPELINE_STAGES = [
    ("REQUEST_RECEIVED", "Request received"),
    ("INTENT_EXTRACTED", "Intent extracted"),
    ("CATALOGUE_SEARCHED", "Catalogue searched"),
    ("UNIT_CONVERSION_CHECKED", "Units, lot sizes & allergens checked"),
    ("PRODUCTS_SHORTLISTED", "Products shortlisted"),
    ("PRICES_RETRIEVED", "Prices verified (Valio API)"),
    ("AVAILABILITY_RETRIEVED", "Availability verified (Valio API)"),
    ("SUBSTITUTIONS_EVALUATED", "Substitutions evaluated"),
    ("RECOMMENDATION_GENERATED", "Ranked"),
    ("POLICY_EVALUATED", "Policy evaluated"),
]
OUTCOME_ACTIONS = {"PROPOSAL_GENERATED": "Proposal created (not submitted to supplier)",
                   "APPROVAL_REQUESTED": "Human approval requested", "REQUEST_BLOCKED": "Blocked",
                   "DUPLICATE_DETECTED": "Blocked as duplicate", "APPROVAL_GRANTED": "Approval granted",
                   "APPROVAL_REJECTED": "Approval rejected", "CHANGES_REQUESTED": "Changes requested",
                   "REVALIDATION_PERFORMED": "Revalidated", "REVALIDATION_FAILED": "Revalidation failed"}


def pipeline(db: Session, req: ProcurementRequest) -> list[dict]:
    """Stages derived only from persisted audit events: a stage is DONE only if the backend recorded it."""
    from app.models import AuditEvent

    events = db.query(AuditEvent).filter(AuditEvent.request_id == req.id).order_by(AuditEvent.ts, AuditEvent.id).all()
    first: dict[str, AuditEvent] = {}
    for e in events:
        first.setdefault(e.action, e)
    out = []
    for action, label in PIPELINE_STAGES:
        e = first.get(action)
        if not e:
            if action == "SUBSTITUTIONS_EVALUATED":
                continue  # only happens when an option fails on availability
            out.append({"stage": action, "label": label, "status": "NOT_REACHED", "ts": None, "event_id": None, "detail": None})
            continue
        status = "DONE"
        if action in ("PRICES_RETRIEVED", "AVAILABILITY_RETRIEVED") and e.decision not in ("OK", None):
            status = "FAILED"
        out.append({"stage": action, "label": label, "status": status, "ts": e.ts.isoformat() if e.ts else None,
                    "event_id": e.id, "detail": e.reason})
    for e in events:
        if e.action in OUTCOME_ACTIONS:
            st = "FAILED" if e.action in ("REQUEST_BLOCKED", "DUPLICATE_DETECTED", "REVALIDATION_FAILED", "APPROVAL_REJECTED") \
                else "WAITING" if e.action in ("APPROVAL_REQUESTED", "CHANGES_REQUESTED") else "DONE"
            out.append({"stage": e.action, "label": OUTCOME_ACTIONS[e.action], "status": st,
                        "ts": e.ts.isoformat() if e.ts else None, "event_id": e.id, "detail": e.reason})
    return out


# ------------------------------------------------------------------ machine-readable decision document
def decision_document(db: Session, req: ProcurementRequest) -> dict:
    recs = db.query(Recommendation).filter(Recommendation.request_id == req.id).all()
    eligible = sorted([json.loads(r.data_json) for r in recs if r.eligible], key=lambda c: c["rank"])
    excluded = [json.loads(r.data_json) for r in recs if not r.eligible]
    selected = next((c for c in eligible if c["id"] == req.selected_product_id), None)
    approval = db.query(Approval).filter(Approval.request_id == req.id).order_by(Approval.requested_at.desc()).first()
    proposal = db.query(Order).filter(Order.request_id == req.id).first()
    pol = db.get(Policy, req.policy_id) if req.policy_id else None
    return {
        "schema": "aimo-protocol/procurement-decision@1",
        "request_id": req.id,
        "organization_id": req.organization_id,
        "correlation_id": req.correlation_id,
        "overrides": json.loads(req.overrides_json or "{}"),
        "pipeline": pipeline(db, req),
        "decision_id": req.decision_id,
        "created_at": req.created_at.isoformat() if req.created_at else None,
        "channel": req.channel,
        "agent": {"id": req.agent_id, "acting_for": req.created_by_name},
        "policy": {"id": req.policy_id, "name": pol.name if pol else None, "version": req.policy_version},
        "request_text": req.raw_text,
        "parser": req.parser_source,
        "intent": json.loads(req.parsed_json or "{}"),
        "status": req.status,
        "decision": req.decision,
        "reasons": json.loads(req.decision_reasons_json or "[]"),
        "human_approval": {
            "required": req.decision == REQUIRES_APPROVAL,
            "approval_id": approval.id if approval else None,
            "status": approval.status if approval else ("NOT_REQUIRED" if req.decision == "ALLOWED" else None),
            "decided_by": approval.decided_by_name if approval else None,
            "decided_at": approval.decided_at.isoformat() if approval and approval.decided_at else None,
            "reason": approval.decision_reason if approval else None,
        },
        "selected": None if not selected else {
            "product_id": selected["id"], "name": selected["product"]["name"],
            "quantity": selected["quantity_sales_units"], "sales_unit": selected["product"]["sales_unit"],
            "unit_price_excl_tax": (selected["price"] or {}).get("unit_price_excl_tax"),
            "total_excl_tax": selected["total_excl_tax"], "total_incl_tax": selected["total_incl_tax"],
            "currency": selected["currency"], "price_status": (selected["price"] or {}).get("status"),
            "availability_status": (selected["availability"] or {}).get("status"),
            "source": selected["source"], "substitute_for": selected.get("substitute_for"),
        },
        "evidence_refs": [e["id"] for e in (selected["evidence"] if selected else [])],
        "evidence": selected["evidence"] if selected else [],
        "proposal": None if not proposal else {
            "id": proposal.id, "status": proposal.status, "submitted_to_supplier": proposal.submitted_to_supplier,
            "total_excl_tax": proposal.total_excl_tax, "currency": proposal.currency,
        },
        "summary": json.loads(req.summary_json or "{}"),
        "comparison": build_comparison(eligible),
        "ranking_explanation": explain_pair(eligible[0], eligible[1]) if len(eligible) >= 2 else None,
        "lifecycle": lifecycle(req, approval, proposal),
        "recommendations": eligible,
        "excluded": excluded,
        "supplier_order_placed": False,
        "disclaimer": DISCLAIMER,
    }
