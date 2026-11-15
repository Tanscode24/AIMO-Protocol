"""Regression tests for recommendation transparency, provenance, ranking explanations,
comparison view and the proposal lifecycle."""
import pytest

from app.services.comparison import build_comparison
from app.services.ranking import explain_pair, rank

PASTA = "Allergen-sensitive kitchen: we need 20 kg of gluten-free pasta, it must be milk-free and egg-free, delivered by Friday."
MILK = "I need 1,000 litres of lactose-free milk by Monday. Find the best option based on price and availability."


@pytest.fixture()
def pasta_doc(client, buyer, mock_valio, fresh_policy):
    mock_valio.default_price = 4.2
    r = client.post("/api/procurement/requests", json={"text": PASTA}, headers=buyer)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- allergen exclusions
def test_allergen_exclusions_verified_per_code_with_evidence(pasta_doc):
    assert pasta_doc["recommendations"], "expected eligible gluten-free pasta"
    for c in pasta_doc["recommendations"]:
        assert c["allergens"]["overall"] == "VERIFIED_SAFE"
        codes = {a["code"]: a["status"] for a in c["allergens"]["checks"]}
        assert codes == {"AW": "FREE_FROM", "AM": "FREE_FROM", "AE": "FREE_FROM"}
        ev_ids = {e["id"] for e in c["evidence"]}
        assert {f"ev:{c['id']}:allergen:{x}" for x in ("AW", "AM", "AE")} <= ev_ids


def test_contains_and_may_contain_products_are_excluded_with_reason(pasta_doc):
    excluded = {x["id"]: " ".join(x["exclusions"]) for x in pasta_doc["excluded"]}
    assert "466511" in excluded  # Zini tagliatelle: AW/AE CONTAINS, AM MAY_CONTAIN
    assert "CONTAINS" in excluded["466511"] and "MAY_CONTAIN" in excluded["466511"]
    assert not any(c["id"] == "466511" for c in pasta_doc["recommendations"])


# ---------------------------------------------------------------- quantity conversion
def test_quantity_block_requested_vs_deliverable(pasta_doc):
    for c in pasta_doc["recommendations"]:
        q = c["quantity"]
        assert q["requested_amount"] == 20 and q["requested_unit"] == "kg"
        assert q["order_quantity"] is not None and q["sales_unit"] == c["product"]["sales_unit"]
        assert q["deliverable_amount"] >= 20 - 1e-9  # never under-deliver
        assert q["surplus"] == pytest.approx(q["deliverable_amount"] - 20, abs=1e-6)
        lot = c["product"]["allowed_lot_size"]
        assert round(q["order_quantity"] / max(lot, 0.01), 6) == round(q["order_quantity"] / max(lot, 0.01))
        assert c["packaging"]["description"].startswith(f"1 {c['product']['sales_unit']}")
        assert q["evidence_ref"] == f"ev:{c['id']}:conversion"


# ---------------------------------------------------------------- price totals & VAT
def test_price_totals_and_vat(pasta_doc):
    for c in pasta_doc["recommendations"]:
        p = c["price"]
        assert p["status"] == "VERIFIED"
        q = c["quantity_sales_units"]
        assert c["total_excl_tax"] == pytest.approx(round(p["unit_price_excl_tax"] * q, 2))
        assert c["total_incl_tax"] == pytest.approx(round(p["unit_price_incl_tax"] * q, 2))
        assert c["total_incl_tax"] > c["total_excl_tax"]
        assert p["vat_rate"] == 13.5 and p["total_excl_tax"] == c["total_excl_tax"]
        assert p["valid_from"].startswith("2026-01-01") and p["valid_to"].startswith("2026-12-31")
        assert p["evidence_ref"] == f"ev:{c['id']}:price"
        ev = next(e for e in c["evidence"] if e["id"] == p["evidence_ref"])
        assert ev["state"] == "VERIFIED" and "VAT 13.5%" in ev["detail"] and ev["timestamp"] and ev["product_id"] == c["id"]


def test_unverified_price_has_no_totals(client, buyer, mock_valio, fresh_policy):
    from app.database import SessionLocal
    from app.models import Product
    with SessionLocal() as s:
        for (pid,) in s.query(Product.id).all():
            mock_valio.price[pid] = {"error": True}
    d = client.post("/api/procurement/requests", json={"text": PASTA + " unpriced"}, headers=buyer).json()
    for c in d["recommendations"]:
        assert c["price"]["status"] == "UNAVAILABLE"  # API returned error=true: no price exists
        assert c["total_excl_tax"] is None and c["total_incl_tax"] is None  # never 0


# ---------------------------------------------------------------- reliability & carbon provenance
def test_reliability_is_historical_not_guarantee(pasta_doc):
    for c in pasta_doc["recommendations"]:
        r = c["reliability_info"]
        assert r["basis"] == "HISTORICAL_DELIVERY_PERFORMANCE" and r["is_guarantee"] is False
        if r["value"] is None:
            assert r["state"] == "UNKNOWN" and "not zero" in r["label"]
        else:
            assert r["state"] == "VERIFIED" and "not a guaranteed delivery" in r["label"]
        assert any("Historical delivery reliability (past performance, not a guarantee)" in x for x in c["reasons"]) \
            or r["value"] is None


def test_missing_reliability_is_unknown_not_zero(client, buyer, mock_valio, fresh_policy):
    d = client.post("/api/procurement/requests", json={"text": MILK + " rel"}, headers=buyer).json()
    missing = [c for c in d["recommendations"] if c["reliability"] is None]
    for c in missing:
        assert c["reliability_info"]["state"] == "UNKNOWN"
        row = next(r for r in c["score_breakdown"] if r["factor"] == "reliability")
        assert row["status"] == "MISSING" and row["contribution"] is None and row["normalised"] is None


def test_carbon_is_group_average(pasta_doc):
    for c in pasta_doc["recommendations"]:
        ci = c["carbon_info"]
        assert ci["basis"] == "PRODUCT_GROUP_AVERAGE" and ci["product_specific"] is False
        if ci["value_per_kg"] is not None:
            assert "product-group average" in ci["label"]
            ev = next(e for e in c["evidence"] if e["id"] == ci["evidence_ref"])
            assert "not a product-specific measurement" in ev["detail"]
        if ci["order_total_kg"] is None:
            assert ci["order_total_status"] == "UNKNOWN" and ci["order_total_reason"]


# ---------------------------------------------------------------- availability dates
def test_availability_dates_and_on_time(pasta_doc):
    for c in pasta_doc["recommendations"]:
        a = c["availability"]
        assert a["requested_date"] == pasta_doc["intent"]["delivery_date"]
        assert a["status"] == "AVAILABLE" and a["on_time"] is True and a["available_date"]


def test_late_product_excluded_with_real_date(client, buyer, mock_valio, fresh_policy):
    from app.database import SessionLocal
    from app.models import Product
    with SessionLocal() as s:
        for (pid,) in s.query(Product.id).all():
            mock_valio.avail[pid] = {"date": "2026-12-30"}
    d = client.post("/api/procurement/requests", json={"text": PASTA + " late"}, headers=buyer).json()
    assert d["recommendations"] == []
    assert any("Late delivery" in " ".join(x["exclusions"]) and "2026-12-30" in " ".join(x["exclusions"]) for x in d["excluded"])


# ---------------------------------------------------------------- ranking with missing factors
W = {"price": 0.4, "reliability": 0.3, "sustainability": 0.2, "match": 0.1}


def c(i, price=None, rel=None, carbon=None, match=1.0):
    return {"id": i, "metrics": {"price": price, "reliability": rel, "sustainability": carbon, "match": match}}


def test_score_breakdown_sums_to_score_and_flags_coverage():
    r = {x["id"]: x for x in rank([c("a", 84.0, None, 1.0, 1.0), c("b", 91.68, 0.98, 1.0, 1.0), c("x", 173.95, 1.0, 2.0, 0.8)], W)}
    for x in r.values():
        total = sum(row["contribution"] for row in x["score_breakdown"] if row["contribution"] is not None)
        assert total == pytest.approx(x["score"] * 100, abs=0.05)
    a = r["a"]
    assert a["evidence_coverage"] == pytest.approx(0.7)
    assert "delivery reliability missing" in a["score_note"] and "Relative score" in a["score_note"]
    row = next(x for x in a["score_breakdown"] if x["factor"] == "reliability")
    assert row["status"] == "MISSING" and row["redistributed_weight"] == 0.3 and row["contribution"] is None
    assert r["b"]["evidence_coverage"] == pytest.approx(1.0)


def test_pairwise_explanation_names_factor_contributions():
    ranked = rank([c("a", 84.0, 0.99, 1.0), c("b", 91.68, 0.99, 1.0)], W)
    for x in ranked:
        x["policy"] = {"decision": "ALLOWED"}
    ex = explain_pair(ranked[0], ranked[1])
    text = " ".join(ex["sentences"])
    assert "Verified price: #1 gains" in text and "84.00 vs 91.68" in text
    assert {f["factor"] for f in ex["factors"]} == {"price", "reliability", "sustainability", "match"}


def test_pairwise_explanation_policy_before_score():
    ranked = rank([c("a", 100.0), c("b", 50.0)], W)
    ranked[0]["policy"], ranked[1]["policy"] = {"decision": "ALLOWED"}, {"decision": "REQUIRES_APPROVAL"}
    ex = explain_pair(ranked[0], ranked[1])
    assert "policy outcome is ALLOWED" in ex["sentences"][0]


# ---------------------------------------------------------------- comparison view
def cmp_cand(i, total=None, status="VERIFIED", rel=None, carbon=None):
    return {"id": i, "rank": 1, "product": {"name": i}, "policy": {"decision": "ALLOWED"},
            "price": {"status": status}, "total_excl_tax": total, "reliability": rel, "carbon_kg_per_kg": carbon,
            "availability": {"status": "AVAILABLE"}, "conversion": {"status": "VERIFIED"},
            "allergens": {"overall": "VERIFIED_SAFE", "checks": []}, "claims": [], "quantity": {}}


def test_comparison_no_fabricated_winners():
    cmp = build_comparison([cmp_cand("a", 84.0, rel=None, carbon=1.2), cmp_cand("b", 91.68, rel=0.98, carbon=1.2),
                            cmp_cand("x", None, status="API_ERROR", rel=None, carbon=1.2)])
    price, rel, carbon = cmp["criteria"]
    assert price["status"] == "COMPARED" and price["best"] == ["a"]  # unverified price never wins
    assert rel["status"] == "INSUFFICIENT_EVIDENCE" and rel["best"] == []  # only one product has data
    assert carbon["status"] == "NO_DIFFERENCE" and carbon["best"] == []  # identical group averages
    row_x = next(r for r in cmp["rows"] if r["product_id"] == "x")
    assert "Price API_ERROR" in row_x["uncertainties"] and "No reliability record" in row_x["uncertainties"]


def test_comparison_ties_are_marked():
    cmp = build_comparison([cmp_cand("a", 50.0), cmp_cand("b", 50.0), cmp_cand("c", 60.0)])
    price = cmp["criteria"][0]
    assert price["tie"] is True and set(price["best"]) == {"a", "b"}


def test_decision_document_contains_comparison_and_explanation(pasta_doc):
    assert pasta_doc["comparison"]["products"] == [c["id"] for c in pasta_doc["recommendations"][:3]]
    if len(pasta_doc["recommendations"]) >= 2:
        ex = pasta_doc["ranking_explanation"]
        assert ex["first"] == pasta_doc["recommendations"][0]["id"] and ex["sentences"]


# ---------------------------------------------------------------- proposal generation & lifecycle
def test_proposal_lines_and_lifecycle(client, buyer, mock_valio, fresh_policy):
    mock_valio.default_price = 0.10
    d = client.post("/api/procurement/requests", json={"text": MILK + " lifecycle"}, headers=buyer).json()
    assert d["status"] == "PROPOSAL_READY"
    stages = {s["stage"]: s for s in d["lifecycle"]}
    assert list(stages) == ["RECOMMENDATION", "POLICY_DECISION", "HUMAN_APPROVAL", "PROCUREMENT_PROPOSAL", "SUPPLIER_ORDER"]
    assert stages["SUPPLIER_ORDER"]["state"] == "NOT_SUPPORTED"
    assert stages["PROCUREMENT_PROPOSAL"]["state"] == "PROPOSAL_READY_NOT_SUBMITTED"
    assert stages["HUMAN_APPROVAL"]["state"] == "NOT_REQUIRED"
    order = client.get(f"/api/orders/{d['proposal']['id']}", headers=buyer).json()
    line = order["lines"][0]
    assert line["submitted_to_supplier"] is False and order["submitted_to_supplier"] is False
    assert line["line_total_excl_tax"] == pytest.approx(round(line["unit_price_excl_tax"] * line["quantity"], 2))
    assert line["tax_rate"] == 13.5 and line["price_valid_to"] and line["requested_date"] and line["on_time"] is True
    assert line["deliverable_amount"] >= 1000 and line["evidence_refs"]
    assert line["reliability_basis"].startswith("HISTORICAL") and line["carbon_basis"] == "PRODUCT_GROUP_AVERAGE"
    assert order["lifecycle"][-1]["state"] == "NOT_SUPPORTED"


def test_approval_lifecycle_states(client, buyer, manager, mock_valio, fresh_policy):
    mock_valio.default_price = 600.0  # any 120 kg option exceeds the 500 autonomous limit
    d = client.post("/api/procurement/requests", json={"text": "We need 120 kg of unsalted butter for Wednesday."}, headers=buyer).json()
    assert d["decision"] == "REQUIRES_APPROVAL"
    stages = {s["stage"]: s["state"] for s in d["lifecycle"]}
    assert stages["HUMAN_APPROVAL"] == "PENDING" and stages["PROCUREMENT_PROPOSAL"] == "NOT_CREATED"
    a = client.post(f"/api/approvals/{d['human_approval']['approval_id']}/approve", json={}, headers=manager).json()
    st = {s["stage"]: s["state"] for s in a["lifecycle"]}
    assert st["HUMAN_APPROVAL"] == "APPROVED" and st["PROCUREMENT_PROPOSAL"] == "PROPOSAL_READY_NOT_SUBMITTED"
    assert st["SUPPLIER_ORDER"] == "NOT_SUPPORTED"


def test_pairwise_identical_values_attributed_to_redistribution():
    ranked = rank([c("a", 84.0, 1.0, None), c("b", 91.68, 1.0, 1.44)], W)
    for x in ranked:
        x["policy"] = {"decision": "ALLOWED"}
    text = " ".join(explain_pair(ranked[0], ranked[1])["sentences"])
    assert "identical value (100.0% (historical))" in text and "weight redistribution" in text
    assert "#1 gains" not in text.split("Delivery reliability")[1].split(".")[0]


def test_comparison_price_caveat_for_different_quantities():
    a = cmp_cand("a", 17.84); a["quantity"] = {"deliverable_amount": 20.0}
    b = cmp_cand("b", 11.20); b["quantity"] = {"deliverable_amount": 24.0}; b["conversion"] = {"status": "ESTIMATED"}
    price = build_comparison([a, b])["criteria"][0]
    assert price["best"] == ["b"] and len(price["caveats"]) == 2
    assert "different deliverable quantities" in price["note"] and "not verified for b" in price["note"]
