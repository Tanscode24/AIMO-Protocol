"""Stock availability safety: supplier placeholder quantities are never presented as confirmed stock.

Live Valio (2026-11-15) returns availableAmount=9999999999 on every Stock product queried; the contract only
documents Terminal=999999. Policy decision (pending Valio confirmation): the configured Stock sentinel means
"quantity NOT confirmed" -> AVAILABLE_QTY_UNCONFIRMED -> human approval (terminal_quantity_action).
"""
from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from app.integrations.valio_client import CallResult, ProductAvailability
from app.services.verification import fmt_qty, interpret_availability

D = date(2026, 11, 16)
SENTINEL = 9999999999


def avail(amount=100000.0, model="Stock", lines=None):
    if lines is None:
        lines = [{"salesBlock": False, "availableAmount": amount, "availableDate": "2026-11-16", "salesUnit": "ST"}]
    pa = {"productId": "1", "closingTime": "2026-11-15T10:00:00", "salesBlock": False, "logisticModel": model, "scheduleLines": lines}
    return CallResult("availability", "OK", items={"1": ProductAvailability.model_validate(pa)}, meta={"timeNow": "2026-11-14T10:00:00"})


def interp(call, qty=50):
    return interpret_availability(call, "1", D, qty, "ST")


# ---------------------------------------------------------------- interpretation
def test_normal_stock_quantity_is_confirmed():
    r = interp(avail(100000))
    assert r["status"] == "AVAILABLE" and r["trust"] == "VERIFIED"
    assert r["available_amount"] == 100000 and r["raw_available_amount"] == 100000
    assert r["quantity_basis"] == "CONFIRMED_STOCK"
    assert "100000 ST available" in r["reason"]


def test_normal_stock_below_requested_quantity_is_partial():
    assert interp(avail(20), qty=50)["status"] == "PARTIAL"


@pytest.mark.parametrize("qty", [50, 1, None])
def test_stock_sentinel_is_never_confirmed_stock(qty):
    r = interp(avail(SENTINEL), qty=qty)
    assert r["status"] == "AVAILABLE_QTY_UNCONFIRMED" and r["trust"] == "UNKNOWN"
    assert r["available_amount"] is None  # never usable as a quantity
    assert r["raw_available_amount"] == SENTINEL  # raw supplier value preserved for evidence
    assert r["quantity_basis"] == "UNCONFIRMED_STOCK_SENTINEL"
    assert r["reason"].startswith("NOT CONFIRMED — supplier availability requires human review.")
    assert "9999999999" not in r["reason"] and "e+" not in r["reason"]  # raw value only in evidence fields


def test_terminal_placeholder_unchanged():
    r = interp(avail(999999, model="Terminal"))
    assert r["status"] == "AVAILABLE_QTY_UNCONFIRMED" and r["available_amount"] is None
    assert r["quantity_basis"] == "TERMINAL_PLACEHOLDER" and "Terminal" in r["reason"]
    # 999999 on a Stock line is also the documented placeholder
    assert interp(avail(999999))["quantity_basis"] == "TERMINAL_PLACEHOLDER"


def test_terminal_model_with_large_amount_is_terminal_not_sentinel():
    r = interp(avail(SENTINEL, model="Terminal"))
    assert r["status"] == "AVAILABLE_QTY_UNCONFIRMED" and r["quantity_basis"] == "TERMINAL_PLACEHOLDER"


def test_zero_stock_is_unavailable():
    r = interp(avail(0))
    assert r["status"] == "UNAVAILABLE" and r["available_amount"] is None


def test_missing_stock():
    assert interp(avail(lines=[]))["status"] == "UNAVAILABLE"  # contract: empty schedule = not deliverable
    missing_product = CallResult("availability", "OK", items={}, meta={})
    r = interpret_availability(missing_product, "1", D, 10, "ST")
    assert r["status"] == "UNKNOWN" and r["trust"] == "UNKNOWN"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -5.0])
def test_malformed_numeric_amount_is_unknown_never_available(bad):
    r = interp(avail(bad))
    assert r["status"] == "UNKNOWN" and r["trust"] == "UNKNOWN" and r["available_amount"] is None
    assert "malformed" in r["reason"]


@pytest.mark.parametrize("line_patch", [{"availableAmount": "lots"}, {"availableAmount": None}, "drop"])
def test_malformed_or_missing_amount_in_raw_response_is_api_error(client, line_patch):
    """Non-numeric, null or absent availableAmount: the response fails validation -> API_ERROR, never available."""
    from app.config import get_settings
    from app.integrations.valio_client import ValioClient

    line = {"salesBlock": False, "availableAmount": 10, "availableDate": "2026-11-16", "salesUnit": "ST"}
    if line_patch == "drop":
        del line["availableAmount"]
    else:
        line.update(line_patch)
    body = {"timeNow": "2026-11-14T10:00:00", "requestedDate": "2026-11-16T00:00:00",
            "products": [{"productId": "1", "closingTime": None, "salesBlock": False, "logisticModel": "Stock", "scheduleLines": [line]}]}
    vc = ValioClient(get_settings(), transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body)))
    call = vc.get_availability(["1"], D)
    r = interpret_availability(call, "1", D, 10, "ST")
    assert call.status == "API_ERROR" and r["status"] == "API_ERROR" and r["trust"] == "ERROR"


def test_confirmed_line_preferred_over_sentinel_line():
    lines = [{"salesBlock": False, "availableAmount": SENTINEL, "availableDate": "2026-11-16", "salesUnit": "ST"},
             {"salesBlock": False, "availableAmount": 500, "availableDate": "2026-11-16", "salesUnit": "ST"}]
    r = interp(avail(lines=lines), qty=50)
    assert r["status"] == "AVAILABLE" and r["available_amount"] == 500 and r["quantity_basis"] == "CONFIRMED_STOCK"


def test_sentinel_interpretation_is_configurable_for_when_valio_confirms(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "valio_stock_quantity_sentinels", "")
    r = interp(avail(SENTINEL))
    assert r["status"] == "AVAILABLE" and r["quantity_basis"] == "CONFIRMED_STOCK"
    assert "9999999999 ST" in r["reason"]  # still never scientific notation


def test_quantity_formatting_never_scientific():
    assert fmt_qty(9999999999.0) == "9999999999"
    assert fmt_qty(1e15) == "1000000000000000"
    assert fmt_qty(12.5) == "12.5" and fmt_qty(0.0001) == "0.0001"


# ---------------------------------------------------------------- end-to-end through the API (mock Valio)
MILK = "I need 1,000 litres of lactose-free milk by Monday, stock sentinel test {n}"


def _all_products_return(mock_valio, amount):
    from app.database import SessionLocal
    from app.models import Product

    with SessionLocal() as s:
        for (pid,) in s.query(Product.id).all():
            mock_valio.avail[pid] = {"amount": amount}


def test_control_real_stock_cheap_request_is_still_allowed(client, buyer, mock_valio, fresh_policy):
    mock_valio.default_price = 0.10
    _all_products_return(mock_valio, 100000)
    d = client.post("/api/procurement/requests", json={"text": MILK.format(n="control")}, headers=buyer).json()
    assert d["decision"] == "ALLOWED" and d["selected"]["availability_status"] == "AVAILABLE"
    assert d["proposal"]["status"] == "PROPOSAL_READY_NOT_SUBMITTED" and d["supplier_order_placed"] is False


def test_sentinel_forces_human_approval_with_traceable_evidence(client, buyer, manager, mock_valio, fresh_policy):
    mock_valio.default_price = 0.10  # cheap: without the sentinel this request would be ALLOWED (see control test)
    _all_products_return(mock_valio, SENTINEL)
    d = client.post("/api/procurement/requests", json={"text": MILK.format(n="approval")}, headers=buyer).json()
    assert d["decision"] == "REQUIRES_APPROVAL", d["reasons"]
    assert "QUANTITY_UNCONFIRMED" in [r["code"] for r in d["reasons"]]
    assert d["selected"]["availability_status"] == "AVAILABLE_QTY_UNCONFIRMED"
    assert d["proposal"] is None and d["supplier_order_placed"] is False

    pid = d["selected"]["product_id"]
    ev = {e["id"]: e for e in d["evidence"]}
    a = ev[f"ev:{pid}:availability"]
    assert a["value"] == "AVAILABLE_QTY_UNCONFIRMED" and a["state"] == "UNKNOWN"
    assert a["source"] == "Valio API POST /api/availability" and a["timestamp"]
    assert "raw availableAmount=9999999999" in a["detail"] and "NOT confirmed" in a["detail"] and "e+" not in a["detail"]
    p = ev[f"ev:{pid}:price"]
    assert p["state"] == "VERIFIED" and p["source"] == "Valio API POST /api/price" and p["timestamp"]
    rec = next(r for r in d["recommendations"] if r["id"] == pid)
    assert rec["availability"]["raw_available_amount"] == SENTINEL and rec["availability"]["available_amount"] is None
    assert "x-api-key" not in json.dumps(d).lower() and "test-valio-key-123456" not in json.dumps(d)

    # A human accepts the quantity risk. Approval re-queries LIVE price and availability before it takes effect;
    # revalidation keeps the same interpretation; the proposal is NOT submitted.
    calls_before = len(mock_valio.calls)
    r = client.post(f"/api/approvals/{d['human_approval']['approval_id']}/approve", json={"reason": "accept"}, headers=manager)
    assert r.status_code == 200 and r.json()["status"] == "APPROVED", r.json()
    assert r.json()["revalidation"]["availability"]["status"] == "AVAILABLE_QTY_UNCONFIRMED"
    recheck = [c["path"] for c in mock_valio.calls[calls_before:]]
    assert "/api/price" in recheck and "/api/availability" in recheck
    doc = client.get(f"/api/procurement/requests/{d['request_id']}", headers=buyer).json()
    assert doc["proposal"]["status"] == "PROPOSAL_READY_NOT_SUBMITTED" and doc["proposal"]["submitted_to_supplier"] is False
    assert doc["supplier_order_placed"] is False


def test_policy_can_block_unconfirmed_quantities(client, buyer, admin, mock_valio, fresh_policy):
    client.put(f"/api/policies/{fresh_policy['id']}", headers=admin, json={"terminal_quantity_action": "BLOCK"})
    mock_valio.default_price = 0.10
    _all_products_return(mock_valio, SENTINEL)
    d = client.post("/api/procurement/requests", json={"text": MILK.format(n="block")}, headers=buyer).json()
    assert d["decision"] == "BLOCKED" and d["proposal"] is None


def test_live_check_endpoint_reports_sentinel_as_unconfirmed(client, buyer, mock_valio):
    mock_valio.avail["464201"] = {"amount": SENTINEL}
    r = client.post("/api/products/464201/live-check", json={"requested_date": "2026-11-16", "quantity": 10}, headers=buyer).json()
    av = r["availability"]
    assert av["status"] == "AVAILABLE_QTY_UNCONFIRMED" and av["available_amount"] is None
    assert av["raw_available_amount"] == SENTINEL and av["quantity_basis"] == "UNCONFIRMED_STOCK_SENTINEL"
    assert av["reason"].startswith("NOT CONFIRMED — supplier availability requires human review.")
    assert "9999999999" not in av["reason"] and "e+" not in av["reason"] and r["price"]["status"] == "VERIFIED"


def test_final_configuration_keeps_the_stock_sentinel():
    """Junction 2026 product decision: 9999999999 stays configured as an unconfirmed Stock sentinel."""
    from pathlib import Path

    from app.config import Settings

    assert Settings(_env_file=None).valio_stock_quantity_sentinels == "9999999999"
    root = Path(__file__).resolve().parents[3]
    for f in (".env.example", "deploy/env/backend.env.example"):
        assert "VALIO_STOCK_QUANTITY_SENTINELS=9999999999" in (root / f).read_text().splitlines(), f


def test_revalidation_failure_blocks_the_proposal(client, buyer, manager, mock_valio, fresh_policy):
    """If the live recheck at approval finds a new problem, no proposal is created."""
    mock_valio.default_price = 0.10
    _all_products_return(mock_valio, SENTINEL)
    d = client.post("/api/procurement/requests", json={"text": MILK.format(n="recheck-fails")}, headers=buyer).json()
    assert d["decision"] == "REQUIRES_APPROVAL"
    mock_valio.avail[d["selected"]["product_id"]] = {"no_lines": True}  # becomes undeliverable before approval
    a = client.post(f"/api/approvals/{d['human_approval']['approval_id']}/approve", json={}, headers=manager).json()
    assert a["status"] == "APPROVED_REVALIDATION_FAILED"
    doc = client.get(f"/api/procurement/requests/{d['request_id']}", headers=buyer).json()
    assert doc["proposal"] is None and doc["supplier_order_placed"] is False
