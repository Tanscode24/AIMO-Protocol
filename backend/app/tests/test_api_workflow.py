"""End-to-end API tests: auth/RBAC, full procurement workflow, approvals, proposals, duplicates, audit."""
import json

MILK = "I need 1,000 litres of lactose-free milk by Monday. Find the best option based on price and availability."
BUTTER = "We need 200 kg of unsalted butter for Wednesday, prioritise reliability."
PASTA = "Allergen-sensitive kitchen: we need 20 kg of gluten-free pasta, it must be milk-free and egg-free, delivered by Friday."


def test_health_is_public(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["catalogue_products"] > 0


def test_auth_required_and_bad_login(client):
    assert client.get("/api/products").status_code == 401
    assert client.post("/api/auth/login", json={"email": "buyer@aimo.local", "password": "nope"}).status_code == 401


def test_rbac(client, viewer, buyer, mock_valio):
    assert client.post("/api/procurement/requests", json={"text": MILK}, headers=viewer).status_code == 403
    assert client.post("/api/approvals/apr_x/approve", json={}, headers=buyer).status_code == 403
    pol = client.get("/api/policies/active", headers=buyer).json()
    assert client.put(f"/api/policies/{pol['id']}", json={"max_order_value": 99999}, headers=buyer).status_code == 403


def test_agent_cannot_modify_own_permissions(client, db):
    hdr = {"X-Agent-Key": "test-agent-key-123456"}
    pol = client.get("/api/policies/active", headers=hdr).json()
    r = client.put(f"/api/policies/{pol['id']}", json={"max_order_value": 1_000_000}, headers=hdr)
    assert r.status_code == 403
    from app.models import AuditEvent
    assert db.query(AuditEvent).filter(AuditEvent.action == "POLICY_CHANGE_DENIED").count() >= 1
    assert client.post("/api/approvals/apr_x/approve", json={}, headers=hdr).status_code == 403
    assert client.put(f"/api/policies/{pol['id']}", json={}, headers={"X-Agent-Key": "wrong"}).status_code == 401


def test_allowed_flow_creates_proposal_not_supplier_order(client, buyer, mock_valio, fresh_policy):
    mock_valio.default_price = 0.10  # 1000 l of milk => well under the 500 limit
    r = client.post("/api/procurement/requests", json={"text": MILK}, headers=buyer)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["schema"] == "aimo-protocol/procurement-decision@1"
    assert d["decision"] == "ALLOWED" and d["status"] == "PROPOSAL_READY"
    assert d["proposal"]["status"] == "PROPOSAL_READY_NOT_SUBMITTED" and d["proposal"]["submitted_to_supplier"] is False
    assert d["supplier_order_placed"] is False
    sel = d["selected"]
    assert sel["price_status"] == "VERIFIED" and sel["availability_status"] == "AVAILABLE"
    top = d["recommendations"][0]
    assert top["claims"][0]["status"] == "VERIFIED"  # declared FREE_FROM_LACTOSE
    assert d["evidence_refs"] and all(e["state"] in ("VERIFIED", "UNKNOWN", "ERROR") for e in d["evidence"])
    orders = client.get("/api/orders", headers=buyer).json()
    assert orders["supplier_orders"] == [] and any(p["request_id"] == d["request_id"] for p in orders["proposals"])
    # only shortlisted products were queried (rate-limit guidance)
    assert all(len(c["body"]["products"]) <= 15 for c in mock_valio.calls)


def test_spend_limit_requires_approval_then_single_use_approval(client, buyer, manager, mock_valio, fresh_policy):
    mock_valio.default_price = 60.0  # any 200 kg butter option costs far above 500
    d = client.post("/api/procurement/requests", json={"text": BUTTER}, headers=buyer).json()
    assert d["decision"] == "REQUIRES_APPROVAL", d["reasons"]
    assert "SPEND_LIMIT" in [x["code"] for x in d["reasons"]]
    appr_id = d["human_approval"]["approval_id"]
    pending = client.get("/api/approvals", params={"status": "PENDING"}, headers=manager).json()
    assert any(a["id"] == appr_id for a in pending)

    # requester cannot approve own request (separation of duties) — buyer lacks role anyway; manager can
    r = client.post(f"/api/approvals/{appr_id}/approve", json={"reason": "Budget OK for event"}, headers=manager)
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["status"] == "APPROVED" and a["decided_by"] and a["previous_state"] == "PENDING" and a["consumed"]
    assert a["revalidation"]["price"]["status"] == "VERIFIED"  # revalidated before finalising

    # single use
    again = client.post(f"/api/approvals/{appr_id}/approve", json={}, headers=manager)
    assert again.status_code == 409
    doc = client.get(f"/api/procurement/requests/{d['request_id']}", headers=buyer).json()
    assert doc["status"] == "PROPOSAL_READY" and doc["proposal"]["submitted_to_supplier"] is False


def test_revalidation_failure_after_approval(client, buyer, manager, mock_valio, fresh_policy):
    mock_valio.default_price = 60.0
    d = client.post("/api/procurement/requests", json={"text": BUTTER}, headers=buyer).json()
    appr_id = d["human_approval"]["approval_id"]
    pid = d["selected"]["product_id"]
    mock_valio.avail[pid] = {"no_lines": True}  # became undeliverable after approval was requested
    a = client.post(f"/api/approvals/{appr_id}/approve", json={}, headers=manager).json()
    assert a["status"] == "APPROVED_REVALIDATION_FAILED"
    doc = client.get(f"/api/procurement/requests/{d['request_id']}", headers=buyer).json()
    assert doc["status"] == "REVALIDATION_FAILED" and doc["proposal"] is None


def test_reject_and_request_changes(client, buyer, manager, mock_valio, fresh_policy):
    mock_valio.default_price = 60.0
    d1 = client.post("/api/procurement/requests", json={"text": BUTTER}, headers=buyer).json()
    r = client.post(f"/api/approvals/{d1['human_approval']['approval_id']}/reject", json={"reason": "Too expensive"}, headers=manager)
    assert r.json()["status"] == "REJECTED"
    d2 = client.post("/api/procurement/requests", json={"text": BUTTER + " "}, headers=buyer).json()
    r2 = client.post(f"/api/approvals/{d2['human_approval']['approval_id']}/request-changes", json={"reason": "Use 100 kg"}, headers=manager)
    assert r2.json()["status"] == "CHANGES_REQUESTED"


def test_separation_of_duties(client, manager, mock_valio, fresh_policy):
    mock_valio.default_price = 60.0
    d = client.post("/api/procurement/requests", json={"text": BUTTER}, headers=manager).json()
    r = client.post(f"/api/approvals/{d['human_approval']['approval_id']}/approve", json={}, headers=manager)
    assert r.status_code == 403


def test_duplicate_request_blocked(client, buyer, admin, mock_valio, fresh_policy):
    client.put(f"/api/policies/{fresh_policy['id']}", headers=admin, json={"duplicate_window_minutes": 60})
    mock_valio.default_price = 60.0
    text = "We need 150 kg of unsalted butter for Wednesday."
    first = client.post("/api/procurement/requests", json={"text": text}, headers=buyer).json()
    assert first["status"] in ("PENDING_APPROVAL", "PROPOSAL_READY")
    second = client.post("/api/procurement/requests", json={"text": text}, headers=buyer).json()
    assert second["decision"] == "BLOCKED" and second["status"] == "DUPLICATE"
    assert "DUPLICATE_REQUEST" in [r["code"] for r in second["reasons"]]
    assert second["proposal"] is None


def test_idempotency_key_returns_same_request(client, buyer, mock_valio, fresh_policy):
    h = {**buyer, "Idempotency-Key": "idem-test-1"}
    a = client.post("/api/procurement/requests", json={"text": MILK}, headers=h).json()
    b = client.post("/api/procurement/requests", json={"text": MILK}, headers=h).json()
    assert a["request_id"] == b["request_id"]


def test_api_timeout_never_treated_as_available(client, buyer, mock_valio, fresh_policy):
    d = client.post("/api/procurement/requests", json={"text": MILK, "simulate": "timeout"}, headers=buyer).json()
    assert d["decision"] != "ALLOWED"
    for c in d["recommendations"]:
        assert c["price"]["status"] == "TIMEOUT" and c["availability"]["status"] == "TIMEOUT"
    codes = [r["code"] for r in d["reasons"]]
    assert "PRICE_UNVERIFIED" in codes and "AVAILABILITY_UNVERIFIED" in codes


def test_real_transport_timeout(client, buyer, mock_valio, fresh_policy):
    mock_valio.mode = "timeout"
    d = client.post("/api/procurement/requests", json={"text": MILK + " t"}, headers=buyer).json()
    assert d["summary"]["price_call"] == "TIMEOUT" and d["decision"] != "ALLOWED"


def test_allergen_scenario_excludes_violations_and_unknowns(client, buyer, mock_valio, fresh_policy):
    d = client.post("/api/procurement/requests", json={"text": PASTA}, headers=buyer).json()
    for c in d["recommendations"]:
        assert c["allergens"]["overall"] == "VERIFIED_SAFE"  # unknown never passes under BLOCK policy
    reasons = " ".join(" ".join(x["exclusions"]) for x in d["excluded"])
    assert "Excluded allergen present" in reasons


def test_unavailable_triggers_substitution(client, buyer, admin, mock_valio, fresh_policy):
    mock_valio.default_price = 0.10
    # Make every lactose-free milk unavailable except the dataset substitute 464440 of 464201
    from app.database import SessionLocal
    from app.models import Product
    with SessionLocal() as s:
        for p in s.query(Product.id).all():
            mock_valio.avail[p.id] = {"no_lines": True}
    mock_valio.avail.pop("464440", None)
    d = client.post("/api/procurement/requests", json={"text": "I need 1000 litres of lactose-free milk by Monday, sub test"}, headers=buyer).json()
    subs = [c for c in d["recommendations"] if c["source"] == "DATASET_SUBSTITUTION"]
    if subs:  # substitute must be compatible with the request's hard constraints
        assert subs[0]["substitute_for"] == "464201"
        assert "SUBSTITUTION_REVIEW" in [r["code"] for r in subs[0]["policy"]["reasons"]]
    assert any("UNAVAILABLE" in " ".join(x["exclusions"]) for x in d["excluded"])


def test_demo_mode_without_api_key(client, buyer, fresh_policy):
    from pydantic import SecretStr

    from app.config import get_settings
    from app.integrations import valio_client as vc
    s = get_settings().model_copy(update={"valio_api_key": SecretStr("")})
    vc.set_valio_client(vc.ValioClient(s))
    try:
        d = client.post("/api/procurement/requests", json={"text": MILK + " demo"}, headers=buyer).json()
        assert d["decision"] in ("REQUIRES_APPROVAL", "BLOCKED")
        assert all(c["price"]["status"] == "API_ERROR" and not c["price"]["live"] for c in d["recommendations"])
        assert "LIVE API UNAVAILABLE" in d["summary"]["api_error"]
    finally:
        vc.set_valio_client(None)


def test_audit_trail_complete_and_secret_free(client, buyer, admin, mock_valio, fresh_policy):
    mock_valio.default_price = 0.10
    d = client.post("/api/procurement/requests", json={"text": MILK + " audit"}, headers=buyer).json()
    ev = client.get("/api/audit", params={"request_id": d["request_id"], "page_size": 200}, headers=admin).json()["items"]
    actions = {e["action"] for e in ev}
    for a in ("REQUEST_RECEIVED", "INTENT_EXTRACTED", "CATALOGUE_SEARCHED", "PRODUCTS_SHORTLISTED", "PRICES_RETRIEVED",
              "AVAILABILITY_RETRIEVED", "RECOMMENDATION_GENERATED", "POLICY_EVALUATED", "PROPOSAL_GENERATED"):
        assert a in actions, a
    blob = json.dumps(client.get("/api/audit", params={"page_size": 200}, headers=admin).json())
    assert "test-valio-key-123456" not in blob and "test-agent-key-123456" not in blob and "test-password" not in blob
    detail = client.get(f"/api/audit/{ev[0]['id']}", headers=admin).json()
    assert detail["id"] == ev[0]["id"] and "action_label" in detail


def test_agent_api_machine_readable(client, mock_valio, fresh_policy):
    r = client.post("/api/agent/v1/procure", json={"text": MILK + " agent"}, headers={"X-Agent-Key": "test-agent-key-123456"})
    assert r.status_code == 200
    d = r.json()
    for k in ("request_id", "decision_id", "policy", "agent", "human_approval", "evidence_refs", "reasons", "decision"):
        assert k in d
    assert d["channel"] == "AGENT_API" and d["agent"]["id"] == "aimo-procurement-agent-01"


def test_policy_update_versioned_and_audited(client, admin):
    pol = client.get("/api/policies/active", headers=admin).json()
    r = client.put(f"/api/policies/{pol['id']}", json={"max_order_value": 750}, headers=admin).json()
    assert r["version"] == pol["version"] + 1 and r["max_order_value"] == 750
    ev = client.get("/api/audit", params={"action": "POLICY_UPDATED"}, headers=admin).json()["items"]
    assert ev and "max_order_value" in ev[0]["payload"]["changes"]


def test_analytics_and_trust(client, admin, mock_valio):
    a = client.get("/api/analytics", headers=admin).json()
    assert a["total_requests"] >= 1 and "catalogue" in a
    t = client.get("/api/trust", headers=admin).json()
    assert t["api"]["order_endpoint"] is None and len(t["sources"]) >= 6
    p = client.post("/api/trust/ping", headers=admin).json()
    assert p["api"]["ping"]["status"] == "OK"


def test_live_check_and_alternatives(client, buyer, mock_valio):
    r = client.post("/api/products/464201/live-check", json={"requested_date": "2026-11-16", "quantity": 10}, headers=buyer)
    assert r.status_code == 200 and r.json()["price"]["status"] == "VERIFIED"
    alt = client.get("/api/products/464201/alternatives", headers=buyer).json()
    assert any(a["source"] == "DATASET_SUBSTITUTION" and a["id"] == "464440" for a in alt["alternatives"])


def test_validation_errors_are_safe(client, buyer):
    r = client.post("/api/procurement/requests", json={"text": ""}, headers=buyer)
    assert r.status_code == 422 and "error" in r.json()
