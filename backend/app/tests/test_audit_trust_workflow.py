"""Hash-chained audit, observed connectivity, assumption confirmation, backend pipeline and migrations."""
import json

from sqlalchemy import create_engine, inspect, text

TEXT = "I need 1,000 litres of lactose-free milk by Monday, audit"


def test_audit_chain_detects_modification_and_deletion(client, admin, buyer, mock_valio, fresh_policy):
    from app.database import SessionLocal, engine
    client.post("/api/procurement/requests", json={"text": TEXT}, headers=buyer)
    v = client.get("/api/admin/audit/verify", headers=admin).json()
    assert v["ok"] is True and v["events_checked"] > 5 and len(v["head_hash"]) == 64
    with engine.begin() as c:
        row = c.execute(text("SELECT * FROM audit_events WHERE seq = 3")).mappings().first()
        c.execute(text("UPDATE audit_events SET reason = 'tampered' WHERE id = :i"), {"i": row["id"]})
    v2 = client.get("/api/admin/audit/verify", headers=admin).json()
    assert v2["ok"] is False and v2["first_invalid_event"] == row["id"] and "modified" in v2["problem"]
    with engine.begin() as c:
        c.execute(text("UPDATE audit_events SET reason = :r WHERE id = :i"), {"r": row["reason"], "i": row["id"]})
        c.execute(text("DELETE FROM audit_events WHERE id = :i"), {"i": row["id"]})
    v3 = client.get("/api/admin/audit/verify", headers=admin).json()
    assert v3["ok"] is False and "gap" in v3["problem"]
    with engine.begin() as c:  # restore so later tests see a valid chain
        c.execute(text(f"INSERT INTO audit_events ({', '.join(row.keys())}) VALUES ({', '.join(':' + k for k in row.keys())})"), dict(row))
    assert client.get("/api/admin/audit/verify", headers=admin).json()["ok"] is True
    with SessionLocal() as s:
        assert s.execute(text("SELECT count(*) FROM audit_events WHERE hash IS NULL")).scalar() == 0


def test_audit_verify_admin_only(client, manager, buyer):
    assert client.get("/api/admin/audit/verify", headers=manager).status_code == 403
    assert client.get("/api/admin/audit/verify", headers=buyer).status_code == 403


def test_audit_events_carry_org_policy_version_and_actor(client, admin, buyer, mock_valio, fresh_policy):
    d = client.post("/api/procurement/requests", json={"text": TEXT + " fields"}, headers=buyer).json()
    ev = client.get("/api/audit", params={"request_id": d["request_id"], "action": "POLICY_EVALUATED"}, headers=admin).json()["items"][0]
    assert ev["organization_id"] == "org_default" and ev["policy_version"] == d["policy"]["version"]
    assert ev["actor_name"] == "Ben Buyer" and ev["hash"] and ev["seq"]


def test_connectivity_reflects_observed_calls(client, admin, mock_valio):
    t = client.post("/api/trust/ping", headers=admin).json()
    assert t["api"]["connectivity"]["state"] == "CONNECTED" and t["api"]["operations"]["ping"]["last_latency_ms"] is not None
    import httpx
    from app.config import get_settings
    from app.integrations import valio_client as vc
    try:
        vc.set_valio_client(vc.ValioClient(get_settings(), transport=httpx.MockTransport(lambda r: httpx.Response(401, json={}))))
        t2 = client.post("/api/trust/ping", headers=admin).json()
        assert t2["api"]["connectivity"]["state"] == "AUTH_FAILED"

        def boom(r):
            raise httpx.ReadTimeout("t", request=r)
        vc.set_valio_client(vc.ValioClient(get_settings(), transport=httpx.MockTransport(boom)))
        assert client.post("/api/trust/ping", headers=admin).json()["api"]["connectivity"]["state"] == "DEGRADED"
        h = client.get("/health").json()
        assert h["connectivity"] == "DEGRADED" and h["mode"] == "LIVE"  # key present is NOT the same as connected
    finally:
        vc.set_valio_client(None)


def test_simulated_failures_do_not_change_connectivity(client, admin, buyer, mock_valio, fresh_policy):
    client.post("/api/trust/ping", headers=admin)
    client.post("/api/procurement/requests", json={"text": TEXT + " sim", "simulate": "timeout"}, headers=buyer)
    t = client.get("/api/trust", headers=admin).json()
    assert t["api"]["connectivity"]["state"] == "CONNECTED" and t["api"]["simulated_events"] >= 1


def test_material_assumptions_require_confirmation(client, buyer, mock_valio, fresh_policy):
    mock_valio.default_price = 0.10
    raw = "we need 40 unsalted butter"  # no unit, no date
    d = client.post("/api/procurement/requests", json={"text": raw}, headers=buyer).json()
    assert d["intent"]["confirmation_required"] and d["request_text"] == raw
    codes = [r["code"] for r in d["reasons"]]
    assert "ASSUMPTION_UNCONFIRMED" in codes and d["decision"] != "ALLOWED"
    c = client.post("/api/procurement/requests", json={"text": raw, "confirm_assumptions": True}, headers=buyer).json()
    assert "ASSUMPTION_UNCONFIRMED" not in [r["code"] for r in c["reasons"]]
    assert c["overrides"]["confirm_assumptions"] is True and c["request_text"] == raw


def test_structured_override_preserved(client, buyer, mock_valio, fresh_policy):
    override = {"product_query": "butter", "display_query": "unsalted butter", "quantity": 10, "unit": "KG",
                "delivery_date": "2026-11-20"}
    d = client.post("/api/procurement/requests", json={"text": "original wording kept", "intent_override": override},
                    headers=buyer).json()
    assert d["parser"] == "PROVIDED" and d["overrides"]["intent_override"] is True and d["request_text"] == "original wording kept"


def test_pipeline_is_backend_confirmed(client, buyer, mock_valio, fresh_policy):
    d = client.post("/api/procurement/requests", json={"text": TEXT + " pipe"}, headers=buyer).json()
    st = {p["stage"]: p for p in d["pipeline"]}
    for s in ("REQUEST_RECEIVED", "INTENT_EXTRACTED", "CATALOGUE_SEARCHED", "PRICES_RETRIEVED", "POLICY_EVALUATED"):
        assert st[s]["status"] == "DONE" and st[s]["event_id"] and st[s]["ts"]
    t = client.post("/api/procurement/requests", json={"text": TEXT + " pipe2", "simulate": "timeout"}, headers=buyer).json()
    assert {p["stage"]: p for p in t["pipeline"]}["PRICES_RETRIEVED"]["status"] == "FAILED"


def test_migration_upgrades_legacy_database(tmp_path):
    """Build a v1.1-shaped database (no organization/hash columns, no new tables), then migrate it."""
    from sqlalchemy import MetaData, Table
    from sqlalchemy.orm import Session

    from app.database import Base, init_db
    from app.services.audit import verify_chain
    new_cols = {"organization_id", "read_scope", "description", "overrides_json", "confirmed_assumptions",
                "correlation_id", "policy_version", "seq", "prev_hash", "hash"}
    new_tables = {"organizations", "agent_keys", "integration_events", "schema_migrations", "auth_sessions",
                  "login_attempts", "rate_limit_hits"}
    eng = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    legacy = MetaData()
    for t in Base.metadata.sorted_tables:
        if t.name in new_tables:
            continue
        Table(t.name, legacy, *[c._copy() for c in t.columns if c.name not in new_cols])
    legacy.create_all(eng)
    with eng.begin() as c:
        c.execute(text("INSERT INTO users (id, email, name, role, password_hash, active, created_at) VALUES "
                       "('u1','m@x','M','PROCUREMENT_MANAGER','h',1,CURRENT_TIMESTAMP),('u2','b@x','B','BUYER','h',1,CURRENT_TIMESTAMP)"))
        c.execute(text("INSERT INTO procurement_requests (id, created_at, raw_text, parsed_json, parser_source, fingerprint, status, "
                       "decision_reasons_json, live_api_used, summary_json, channel) VALUES "
                       "('r1', CURRENT_TIMESTAMP, 'legacy', '{}', 'RULES', 'f', 'BLOCKED', '[]', 0, '{}', 'WORKSPACE')"))
        c.execute(text("INSERT INTO audit_events (id, ts, actor_type, action, request_id, evidence_json, payload_json) VALUES "
                       "('e1','2026-11-01 10:00:00.000000','USER','REQUEST_RECEIVED','r1','[]','{}'),"
                       "('e2','2026-11-01 10:00:01.000000','USER','POLICY_EVALUATED','r1','[]','{}')"))
    applied = init_db(eng)
    assert applied == ["001_organizations_and_read_scope", "002_request_overrides_and_correlation", "003_audit_hash_chain",
                       "004_auth_sessions_and_login_throttle", "005_shared_rate_limit",
                       "006_pg_trigram_search"]
    assert init_db(eng) == []  # idempotent
    assert {"auth_sessions", "login_attempts", "rate_limit_hits"} <= set(inspect(eng).get_table_names())
    with eng.connect() as c:
        assert c.execute(text("SELECT organization_id FROM procurement_requests WHERE id='r1'")).scalar() == "org_default"
        assert dict(c.execute(text("SELECT email, read_scope FROM users")).all()) == {"m@x": "ORG", "b@x": "OWN"}
        assert c.execute(text("SELECT raw_text FROM procurement_requests")).scalar() == "legacy"  # data preserved
        assert c.execute(text("SELECT organization_id FROM audit_events WHERE id='e1'")).scalar() == "org_default"
    with Session(eng) as s:
        v = verify_chain(s)
        assert v["ok"] is True and v["events_checked"] == 2
