"""External agent gateway: per-agent keys, scopes, revocation, rotation, expiry, tenant scoping, rate limits,
correlation IDs, structured errors, idempotency, published JSON Schemas, OpenAPI and the MCP adapter."""
import json
from datetime import timedelta
from pathlib import Path

import jsonschema
import pytest

from app.tests.conftest import AGENT

MILK = "I need 1,000 litres of lactose-free milk by Monday, gateway"
ROOT = Path(__file__).resolve().parents[3]


def _new_agent(client, admin, scopes, name="Test agent"):
    a = client.post("/api/admin/agents", json={"name": name}, headers=admin)
    assert a.status_code == 201, a.text
    k = client.post(f"/api/admin/agents/{a.json()['id']}/keys", json={"scopes": scopes, "label": "t"}, headers=admin)
    assert k.status_code == 201, k.text
    return a.json(), k.json()


def test_bootstrap_key_identity(client):
    me = client.get("/api/agent/v1/me", headers=AGENT).json()
    assert me["agent_id"] == "aimo-procurement-agent-01" and me["organization_id"] == "org_default"
    assert "procure:create" in me["scopes"]


def test_per_agent_key_issue_scope_revoke(client, admin, mock_valio, fresh_policy):
    agent, key = _new_agent(client, admin, ["catalogue:read"])
    h = {"X-Agent-Key": key["api_key"]}
    assert key["api_key"].startswith("aimo_ak_") and "warning" in key
    assert client.get("/api/agent/v1/catalogue/search", params={"q": "milk"}, headers=h).status_code == 200
    r = client.post("/api/agent/v1/procure", json={"text": MILK}, headers=h)
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"  # least privilege: no procure scope
    # the key is never stored in plaintext
    from app.database import SessionLocal
    from app.models import AgentKey
    with SessionLocal() as s:
        stored = s.get(AgentKey, key["id"])
        assert stored.key_hash != key["api_key"] and key["api_key"] not in json.dumps(stored.__dict__, default=str)
    rev = client.post(f"/api/admin/agents/{agent['id']}/keys/{key['id']}/revoke", headers=admin)
    assert rev.json()["status"] == "REVOKED"
    assert client.get("/api/agent/v1/me", headers=h).status_code == 401


def test_key_rotation(client, admin):
    agent, key = _new_agent(client, admin, ["catalogue:read", "procure:read"])
    new = client.post(f"/api/admin/agents/{agent['id']}/keys/{key['id']}/rotate", headers=admin).json()
    assert client.get("/api/agent/v1/me", headers={"X-Agent-Key": key["api_key"]}).status_code == 401
    me = client.get("/api/agent/v1/me", headers={"X-Agent-Key": new["api_key"]})
    assert me.status_code == 200 and set(me.json()["scopes"]) == {"catalogue:read", "procure:read"}


def test_expired_key_rejected(client, admin):
    from app.database import SessionLocal
    from app.models import AgentKey, utcnow
    _, key = _new_agent(client, admin, ["catalogue:read"])
    with SessionLocal() as s:
        s.get(AgentKey, key["id"]).expires_at = utcnow() - timedelta(minutes=1)
        s.commit()
    assert client.get("/api/agent/v1/me", headers={"X-Agent-Key": key["api_key"]}).status_code == 401


def test_invalid_scope_rejected(client, admin):
    a = client.post("/api/admin/agents", json={"name": "x"}, headers=admin).json()
    r = client.post(f"/api/admin/agents/{a['id']}/keys", json={"scopes": ["admin:everything"]}, headers=admin)
    assert r.status_code == 422


def test_non_admin_cannot_manage_agents(client, manager, buyer):
    for h in (manager, buyer):
        assert client.post("/api/admin/agents", json={"name": "x"}, headers=h).status_code == 403


def test_agent_of_other_tenant_isolated(client, mock_valio, fresh_policy):
    """An agent in org_nordic with full scopes cannot see org_default agent requests."""
    from app.database import SessionLocal
    from app.models import Agent, AgentKey
    from app.security import generate_agent_key
    d = client.post("/api/agent/v1/procure", json={"text": MILK + " tenant"}, headers=AGENT).json()
    plain, prefix, digest = generate_agent_key()
    with SessionLocal() as s:
        if not s.get(Agent, "agt_nordic_test"):
            s.add(Agent(id="agt_nordic_test", name="Nordic agent", organization_id="org_nordic"))
        s.add(AgentKey(agent_id="agt_nordic_test", prefix=prefix, key_hash=digest,
                       scopes_json='["catalogue:read","procure:create","procure:read"]'))
        s.commit()
    h = {"X-Agent-Key": plain}
    r = client.get(f"/api/agent/v1/decisions/{d['request_id']}", headers=h)
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"


def test_rate_limit_429_with_retry_after(client):
    from app.api.agent_gateway import limiter
    from app.config import get_settings
    s = get_settings()
    old = s.agent_rate_limit_per_minute
    s.agent_rate_limit_per_minute = 3
    limiter.reset()
    try:
        codes = [client.get("/api/agent/v1/me", headers=AGENT).status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]
        r = client.get("/api/agent/v1/me", headers=AGENT)
        assert r.json()["code"] == "RATE_LIMITED" and int(r.headers["Retry-After"]) >= 1
    finally:
        s.agent_rate_limit_per_minute = old
        limiter.reset()


def test_correlation_id_propagates_to_request_and_audit(client, admin, mock_valio, fresh_policy):
    r = client.post("/api/agent/v1/procure", json={"text": MILK + " corr"}, headers={**AGENT, "X-Correlation-ID": "ext-123.abc"})
    assert r.headers["X-Correlation-ID"] == "ext-123.abc" and r.json()["correlation_id"] == "ext-123.abc"
    ev = client.get("/api/audit", params={"request_id": r.json()["request_id"], "page_size": 50}, headers=admin).json()["items"]
    assert ev and all(e["correlation_id"] == "ext-123.abc" for e in ev)
    bad = client.get("/api/agent/v1/me", headers={**AGENT, "X-Correlation-ID": "<script>"})
    assert bad.headers["X-Correlation-ID"].startswith("cor_")


def test_structured_errors(client):
    r = client.get("/api/agent/v1/decisions/req_doesnotexist", headers=AGENT)
    body = r.json()
    assert r.status_code == 404 and set(body) >= {"error", "code", "correlation_id"}
    v = client.post("/api/agent/v1/procure", json={"text": ""}, headers=AGENT)
    assert v.status_code == 422 and v.json()["code"] == "VALIDATION_ERROR" and v.json()["details"]
    assert client.get("/api/agent/v1/me", headers={"X-Agent-Key": "nope"}).json()["code"] == "UNAUTHENTICATED"


def test_agent_idempotency(client, mock_valio, fresh_policy):
    h = {**AGENT, "Idempotency-Key": "agent-idem-1"}
    a = client.post("/api/agent/v1/procure", json={"text": MILK + " idem"}, headers=h).json()
    b = client.post("/api/agent/v1/procure", json={"text": MILK + " idem"}, headers=h).json()
    assert a["request_id"] == b["request_id"]


def test_response_validates_against_published_schema(client, mock_valio, fresh_policy):
    schema = json.loads((ROOT / "schemas" / "procurement-decision.v1.json").read_text())
    for text in (MILK + " schema", "We need 200 kg of unsalted butter for Wednesday, schema"):
        doc = client.post("/api/agent/v1/procure", json={"text": text}, headers=AGENT).json()
        jsonschema.validate(doc, schema)
        assert doc["supplier_order_placed"] is False


def test_published_schemas_are_current():
    from scripts.export_schemas import build
    for fname, content in build().items():
        path = ROOT / "schemas" / fname
        assert path.exists(), f"missing {fname}: run python -m scripts.export_schemas"
        assert json.loads(path.read_text()) == json.loads(json.dumps(content, sort_keys=True)), \
            f"{fname} is out of date: run python -m scripts.export_schemas"


def test_schemas_endpoint_and_openapi(client):
    names = [s["name"] for s in client.get("/api/agent/v1/schemas").json()["schemas"]]
    assert "procurement-decision.v1" in names
    assert client.get("/api/agent/v1/schemas/procurement-decision.v1").json()["$id"].endswith("procurement-decision.v1.json")
    spec = client.get("/openapi.json").json()
    op = spec["paths"]["/api/agent/v1/procure"]["post"]
    assert "ProcurementDecisionV1" in json.dumps(op["responses"]["200"]) and "429" in op["responses"]


def test_mcp_adapter(client, mock_valio, fresh_policy):
    from aimo_mcp.server import AimoMcp
    m = AimoMcp("http://testserver", "test-agent-key-123456", http=client)
    assert m.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})["result"]["serverInfo"]["name"] == "aimo-protocol"
    tools = {t["name"] for t in m.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]}
    assert tools == {"search_catalogue", "procure", "get_decision"}
    res = m.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "procure", "arguments": {"text": MILK + " mcp"}}})["result"]
    out = json.loads(res["content"][0]["text"])
    assert res["isError"] is False and out["decision"] in ("ALLOWED", "REQUIRES_APPROVAL", "BLOCKED")
    assert out["supplier_order_placed"] is False
    assert m.handle({"jsonrpc": "2.0", "id": 4, "method": "nope"})["error"]["code"] == -32601
    assert m.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    bad = AimoMcp("http://testserver", "wrong-key", http=client)
    r = bad.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "get_decision", "arguments": {"request_id": "x"}}})
    assert r["result"]["isError"] is True and "401" in r["result"]["content"][0]["text"]
