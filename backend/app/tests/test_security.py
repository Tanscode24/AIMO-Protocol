"""P0/P1 security regression tests — direct API calls, no frontend.

Covers record ownership (IDOR), tenant isolation, agent scoping, separation of duties, single-use approvals,
idempotency-key isolation, audit scoping and denied-access auditing."""
import pytest

from app.tests.conftest import AGENT

TEXT = "We need 120 kg of unsalted butter for Wednesday."  # always REQUIRES_APPROVAL at price 600 (see fixture)


@pytest.fixture()
def owned(client, buyer, mock_valio, fresh_policy):
    """A request owned by buyer@aimo.local (org_default) that is pending approval."""
    mock_valio.default_price = 600.0
    d = client.post("/api/procurement/requests", json={"text": TEXT}, headers=buyer).json()
    assert d["decision"] == "REQUIRES_APPROVAL", d["reasons"]
    return d


def _denied_events(client, admin, record_id):
    items = client.get("/api/audit", params={"action": "ACCESS_DENIED", "page_size": 200}, headers=admin).json()["items"]
    return [e for e in items if record_id in (e["reason"] or "")]


# ---------------------------------------------------------------- IDOR between buyers (same organization)
def test_buyer_cannot_read_another_buyers_request(client, owned, buyer2, admin):
    r = client.get(f"/api/procurement/requests/{owned['request_id']}", headers=buyer2)
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"
    assert _denied_events(client, admin, owned["request_id"]), "denied access must be audited"


def test_buyer_cannot_decide_on_another_buyers_request(client, owned, buyer2):
    r = client.post(f"/api/procurement/requests/{owned['request_id']}/decide", json={}, headers=buyer2)
    assert r.status_code == 404


def test_buyer_list_contains_only_own_requests(client, owned, buyer2):
    ids = [x["id"] for x in client.get("/api/procurement/requests", params={"page_size": 100}, headers=buyer2).json()["items"]]
    assert owned["request_id"] not in ids


def test_buyer_cannot_read_other_approvals_proposals_or_audit(client, owned, buyer2):
    appr = owned["human_approval"]["approval_id"]
    assert client.get(f"/api/approvals/{appr}", headers=buyer2).status_code == 404
    assert all(a["id"] != appr for a in client.get("/api/approvals", headers=buyer2).json())
    ev = client.get("/api/audit", params={"request_id": owned["request_id"]}, headers=buyer2).json()
    assert ev["total"] == 0 and ev["items"] == []
    any_event = client.get("/api/audit", params={"request_id": owned["request_id"]},
                           headers=client.headers).status_code  # unauthenticated
    assert any_event == 401


def test_buyer_cannot_read_other_buyers_audit_event_by_id(client, owned, buyer, buyer2):
    eid = client.get("/api/audit", params={"request_id": owned["request_id"]}, headers=buyer).json()["items"][0]["id"]
    assert client.get(f"/api/audit/{eid}", headers=buyer).status_code == 200
    assert client.get(f"/api/audit/{eid}", headers=buyer2).status_code == 404


def test_owner_still_has_full_access(client, owned, buyer):
    assert client.get(f"/api/procurement/requests/{owned['request_id']}", headers=buyer).status_code == 200
    assert client.get(f"/api/approvals/{owned['human_approval']['approval_id']}", headers=buyer).status_code == 200


def test_buyer_proposals_are_private(client, buyer, buyer2, mock_valio, fresh_policy):
    mock_valio.default_price = 0.10
    d = client.post("/api/procurement/requests", json={"text": "I need 1,000 litres of lactose-free milk by Monday, sec"},
                    headers=buyer).json()
    assert d["proposal"], d["reasons"]
    pid = d["proposal"]["id"]
    assert client.get(f"/api/orders/{pid}", headers=buyer).status_code == 200
    assert client.get(f"/api/orders/{pid}", headers=buyer2).status_code == 404
    assert all(p["id"] != pid for p in client.get("/api/orders", headers=buyer2).json()["proposals"])


# ---------------------------------------------------------------- organization-wide roles
def test_manager_and_viewer_see_org_records_read_only(client, owned, manager, viewer):
    assert client.get(f"/api/procurement/requests/{owned['request_id']}", headers=manager).status_code == 200
    assert client.get(f"/api/procurement/requests/{owned['request_id']}", headers=viewer).status_code == 200
    # viewer is read-only
    assert client.post(f"/api/approvals/{owned['human_approval']['approval_id']}/approve", json={}, headers=viewer).status_code == 403
    assert client.post("/api/procurement/requests", json={"text": TEXT}, headers=viewer).status_code == 403


def test_manager_cannot_change_a_buyers_selection(client, owned, manager):
    r = client.post(f"/api/procurement/requests/{owned['request_id']}/decide", json={}, headers=manager)
    assert r.status_code == 403  # may view, may approve, but not modify another user's request


# ---------------------------------------------------------------- tenant isolation
def test_other_organization_cannot_see_anything(client, owned, nordic_buyer, nordic_manager):
    rid, appr = owned["request_id"], owned["human_approval"]["approval_id"]
    for h in (nordic_buyer, nordic_manager):
        assert client.get(f"/api/procurement/requests/{rid}", headers=h).status_code == 404
        assert client.get(f"/api/approvals/{appr}", headers=h).status_code == 404
        assert client.get("/api/audit", params={"request_id": rid}, headers=h).json()["total"] == 0
        assert rid not in [x["id"] for x in client.get("/api/procurement/requests", params={"page_size": 100}, headers=h).json()["items"]]
    # a manager of another organization cannot approve
    r = client.post(f"/api/approvals/{appr}/approve", json={}, headers=nordic_manager)
    assert r.status_code == 404
    assert client.get(f"/api/approvals/{appr}", headers=client.headers).status_code == 401


def test_other_organization_cannot_modify_policy(client, admin, nordic_manager):
    pol = client.get("/api/policies/active", headers=admin).json()
    assert client.put(f"/api/policies/{pol['id']}", json={"max_order_value": 1}, headers=nordic_manager).status_code == 403


def test_policies_are_per_organization(client, admin, nordic_manager):
    a = client.get("/api/policies/active", headers=admin).json()
    b = client.get("/api/policies/active", headers=nordic_manager).json()
    assert a["id"] != b["id"]


def test_analytics_scoped(client, owned, buyer2, nordic_buyer, admin):
    org_total = client.get("/api/analytics", headers=admin).json()["total_requests"]
    assert client.get("/api/analytics", headers=buyer2).json()["scope"]["label"] == "Your own requests"
    assert client.get("/api/analytics", headers=nordic_buyer).json()["total_requests"] < org_total


# ---------------------------------------------------------------- approvals
def test_approval_separation_of_duties_and_single_use(client, owned, buyer, manager):
    appr = owned["human_approval"]["approval_id"]
    assert client.post(f"/api/approvals/{appr}/approve", json={}, headers=buyer).status_code == 403  # no permission
    r = client.post(f"/api/approvals/{appr}/approve", json={"reason": "ok"}, headers=manager)
    assert r.status_code == 200
    assert client.post(f"/api/approvals/{appr}/approve", json={}, headers=manager).status_code == 409
    assert client.post(f"/api/approvals/{appr}/reject", json={}, headers=manager).status_code == 409


def test_manager_cannot_approve_own_request(client, manager, mock_valio, fresh_policy):
    mock_valio.default_price = 600.0
    d = client.post("/api/procurement/requests", json={"text": "We need 130 kg of unsalted butter for Wednesday."}, headers=manager).json()
    r = client.post(f"/api/approvals/{d['human_approval']['approval_id']}/approve", json={}, headers=manager)
    assert r.status_code == 403 and "Separation of duties" in r.json()["error"]


def test_agent_owner_cannot_approve_agent_request(client, mock_valio, fresh_policy):
    """buyer@aimo.local owns the default agent; a manager-role owner would also be blocked by the same rule."""
    from app.database import SessionLocal
    from app.models import Agent, User
    mock_valio.default_price = 600.0
    d = client.post("/api/agent/v1/procure", json={"text": "We need 140 kg of unsalted butter for Wednesday."}, headers=AGENT).json()
    appr = d["human_approval"]["approval_id"]
    with SessionLocal() as s:
        mgr = s.query(User).filter_by(email="manager@aimo.local").first()
        ag = s.get(Agent, "aimo-procurement-agent-01")
        old_owner, ag.owner_user_id = ag.owner_user_id, mgr.id
        s.commit()
    try:
        from app.tests.conftest import login
        r = client.post(f"/api/approvals/{appr}/approve", json={}, headers=login(client, "manager@aimo.local"))
        assert r.status_code == 403 and "own the agent" in r.json()["error"]
    finally:
        with SessionLocal() as s:
            s.get(Agent, "aimo-procurement-agent-01").owner_user_id = old_owner
            s.commit()


# ---------------------------------------------------------------- agents
def test_agent_sees_only_its_own_requests(client, owned, mock_valio, fresh_policy):
    assert client.get(f"/api/agent/v1/decisions/{owned['request_id']}", headers=AGENT).status_code == 404
    d = client.post("/api/agent/v1/procure", json={"text": "I need 30 kg of unsalted butter for Wednesday."}, headers=AGENT).json()
    assert client.get(f"/api/agent/v1/decisions/{d['request_id']}", headers=AGENT).status_code == 200
    ids = [x["id"] for x in client.get("/api/procurement/requests", params={"page_size": 100}, headers=AGENT).json()["items"]]
    assert owned["request_id"] not in ids and d["request_id"] in ids


def test_agent_cannot_approve_or_administer(client, owned):
    assert client.post(f"/api/approvals/{owned['human_approval']['approval_id']}/approve", json={}, headers=AGENT).status_code == 403
    assert client.get("/api/admin/agents", headers=AGENT).status_code == 403
    assert client.get("/api/admin/audit/verify", headers=AGENT).status_code == 403


def test_idempotency_keys_are_isolated_between_principals(client, buyer, buyer2, mock_valio, fresh_policy):
    h1, h2 = {**buyer, "Idempotency-Key": "shared-key-1"}, {**buyer2, "Idempotency-Key": "shared-key-1"}
    a = client.post("/api/procurement/requests", json={"text": "we need 12 kg of unsalted butter tomorrow"}, headers=h1).json()
    b = client.post("/api/procurement/requests", json={"text": "we need 12 kg of unsalted butter tomorrow"}, headers=h2).json()
    assert a["request_id"] != b["request_id"]  # never returns another principal's record
    again = client.post("/api/procurement/requests", json={"text": "different"}, headers=h1).json()
    assert again["request_id"] == a["request_id"]


def test_tokens_cannot_be_forged_and_roles_come_from_db(client):
    from app.security import _b64
    import json as _json
    fake = _b64(_json.dumps({"sub": "usr_x", "role": "ADMIN", "exp": 9999999999}).encode()) + ".AAAA"
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {fake}"}).status_code == 401


def test_cookie_session_requires_csrf_header(client, mock_valio, fresh_policy):
    c = client
    r = c.post("/api/auth/login", json={"email": "buyer@aimo.local", "password": "test-password"})
    cookie = r.cookies.get("aimo_session")
    assert cookie and "httponly" in r.headers["set-cookie"].lower()
    jar = {"aimo_session": cookie}
    assert c.get("/api/auth/me", cookies=jar).status_code == 200
    assert c.post("/api/procurement/parse", json={"text": "10 kg butter"}, cookies=jar).status_code == 403
    ok = c.post("/api/procurement/parse", json={"text": "10 kg butter"}, cookies=jar, headers={"X-Requested-With": "aimo-web"})
    assert ok.status_code == 200
    c.cookies.clear()


def test_secrets_never_in_responses_or_audit(client, admin, mock_valio):
    import json as _json
    blob = _json.dumps(client.get("/api/audit", params={"page_size": 200}, headers=admin).json())
    blob += _json.dumps(client.get("/api/trust", headers=admin).json())
    for secret in ("test-valio-key-123456", "test-agent-key-123456", "test-password", "test-auth-secret-abcdef"):
        assert secret not in blob
