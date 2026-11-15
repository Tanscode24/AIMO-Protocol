from datetime import date

import httpx
import pytest
from pydantic import SecretStr

from app.config import get_settings
from app.integrations.valio_client import CallResult, ProductAvailability, ProductPrice, ValioClient
from app.services.verification import interpret_availability, interpret_price

D = date(2026, 11, 16)


def make_client(handler, key="test-valio-key-123456"):
    s = get_settings().model_copy(update={"valio_api_key": SecretStr(key), "valio_cache_ttl_seconds": 60})
    return ValioClient(s, transport=httpx.MockTransport(handler))


def ok_price(request):
    import json
    body = json.loads(request.content)
    return httpx.Response(200, json={"customerId": body["customer"], "currency": "EUR", "pricingDate": body["requestedDate"],
                                     "products": [{"productId": p, "quantity": 1.0, "netPriceFound": False, "error": False,
                                                   "catalogPrice": {"value": 1.5, "valueWithTax": 1.7, "validFrom": "2026-01-01",
                                                                    "validTo": "2026-12-31", "currency": "EUR"}}
                                                  for p in body["products"]]})


# ---------------------------------------------------------------- client
def test_request_follows_openapi_contract():
    seen = {}

    def h(req):
        import json
        seen["path"], seen["key"], seen["body"] = req.url.path, req.headers.get("x-api-key"), json.loads(req.content)
        return ok_price(req)

    r = make_client(h).get_prices(["464201", "464179"], D)
    assert r.status == "OK"
    assert seen["path"] == "/api/price"
    assert seen["key"] == "test-valio-key-123456"
    assert set(seen["body"]) == {"customer", "requestedDate", "products"}
    assert seen["body"]["requestedDate"] == "2026-11-16" and seen["body"]["products"] == ["464201", "464179"]


def test_chunking_above_1000_products():
    sizes = []

    def h(req):
        import json
        sizes.append(len(json.loads(req.content)["products"]))
        return ok_price(req)

    r = make_client(h).get_prices([str(460000 + i) for i in range(1500)], D)
    assert sizes == [1000, 500] and len(r.items) == 1500


def test_cache_avoids_repeat_calls():
    n = {"c": 0}

    def h(req):
        n["c"] += 1
        return ok_price(req)

    c = make_client(h)
    c.get_prices(["464201"], D)
    r = c.get_prices(["464201"], D)
    assert n["c"] == 1 and r.from_cache == 1


@pytest.mark.parametrize("status,expected", [(422, "422"), (429, "rate limit"), (500, "HTTP 500"), (401, "rejected")])
def test_http_errors(status, expected):
    r = make_client(lambda req: httpx.Response(status, json={})).get_prices(["1"], D)
    assert r.status == "API_ERROR" and expected in r.error


def test_timeout():
    def h(req):
        raise httpx.ReadTimeout("t", request=req)

    r = make_client(h).get_availability(["1"], D)
    assert r.status == "TIMEOUT"


def test_malformed_response():
    r = make_client(lambda req: httpx.Response(200, json={"foo": 1})).get_prices(["1"], D)
    assert r.status == "API_ERROR" and "schema" in r.error


def test_not_configured_is_demo_mode():
    c = make_client(ok_price, key="")
    r = c.get_prices(["1"], D)
    assert r.status == "API_ERROR" and not r.live and "LIVE API UNAVAILABLE" in r.error


def test_simulated_timeout():
    r = make_client(ok_price).get_prices(["1"], D, simulate="timeout")
    assert r.status == "TIMEOUT" and r.simulated


def test_api_key_never_in_error_messages():
    r = make_client(lambda req: httpx.Response(401, json={})).get_prices(["1"], D)
    assert "test-valio-key" not in (r.error or "")


# ---------------------------------------------------------------- price interpretation
def price_call(**pp):
    base = {"productId": "1", "quantity": 1.0, "netPriceFound": False, "error": False,
            "catalogPrice": {"value": 2.0, "valueWithTax": 2.27, "validFrom": "2026-01-01", "validTo": "2026-12-31", "currency": "EUR"},
            "tax": {"taxRate": 13.5, "taxValue": 0.27}}
    base.update(pp)
    return CallResult("price", "OK", items={"1": ProductPrice.model_validate(base)}, meta={"currency": "EUR"})


def test_price_verified():
    r = interpret_price(price_call(), "1", D)
    assert r["status"] == "VERIFIED" and r["unit_price_excl_tax"] == 2.0 and r["tax_rate"] == 13.5 and r["trust"] == "VERIFIED"


def test_price_error_flag_is_unverified():
    # error=true means "no price exists" -> UNAVAILABLE (distinct from an invalid price, never VERIFIED)
    r = interpret_price(price_call(error=True, catalogPrice=None), "1", D)
    assert r["status"] == "UNAVAILABLE" and r["trust"] == "UNKNOWN"


def test_price_missing_product():
    r = interpret_price(CallResult("price", "OK"), "1", D)
    assert r["status"] == "UNAVAILABLE" and r["status"] != "VERIFIED"


def test_price_validity_outside_date():
    cp = {"value": 2.0, "valueWithTax": 2.27, "validFrom": "2025-01-01", "validTo": "2025-12-31", "currency": "EUR"}
    assert interpret_price(price_call(catalogPrice=cp), "1", D)["status"] == "UNVERIFIED"


def test_price_api_failure_states():
    assert interpret_price(CallResult("price", "TIMEOUT", error="t"), "1", D)["status"] == "TIMEOUT"
    assert interpret_price(CallResult("price", "API_ERROR", error="e"), "1", D)["trust"] == "ERROR"


# ---------------------------------------------------------------- availability interpretation
def avail(model="Stock", amount=100.0, day="2026-11-16", lines=True, block=False, line_block=False, unit="ST"):
    pa = {"productId": "1", "closingTime": "2026-11-15T10:00:00", "salesBlock": block, "logisticModel": model,
          "scheduleLines": [{"salesBlock": line_block, "availableAmount": amount, "availableDate": day, "salesUnit": unit}] if lines else []}
    return CallResult("availability", "OK", items={"1": ProductAvailability.model_validate(pa)}, meta={"timeNow": "2026-11-14T10:00:00"})


@pytest.mark.parametrize("kwargs,qty,expected", [
    ({}, 50, "AVAILABLE"),
    ({"amount": 20}, 50, "PARTIAL"),
    ({"amount": 0}, 50, "UNAVAILABLE"),
    ({"lines": False}, 50, "UNAVAILABLE"),
    ({"block": True}, 50, "UNAVAILABLE"),
    ({"line_block": True}, 50, "UNAVAILABLE"),
    ({"day": "2026-11-18"}, 50, "LATE"),
    ({"model": "Terminal", "amount": 999999}, 50, "AVAILABLE_QTY_UNCONFIRMED"),
    ({"model": "Stock", "amount": 999999}, 50, "AVAILABLE_QTY_UNCONFIRMED"),
    ({"unit": "KI"}, 50, "UNKNOWN"),
])
def test_availability_states(kwargs, qty, expected):
    r = interpret_availability(avail(**kwargs), "1", D, qty, "ST")
    assert r["status"] == expected, r["reason"]


def test_terminal_999999_never_reported_as_stock():
    r = interpret_availability(avail(model="Terminal", amount=999999), "1", D, 10, "ST")
    assert r["available_amount"] is None and r["trust"] == "UNKNOWN"


def test_missing_product_and_api_failure():
    assert interpret_availability(CallResult("availability", "OK"), "1", D, 1, "ST")["status"] == "UNKNOWN"
    assert interpret_availability(CallResult("availability", "TIMEOUT", error="t"), "1", D, 1, "ST")["status"] == "TIMEOUT"
    assert interpret_availability(CallResult("availability", "API_ERROR", error="x"), "1", D, 1, "ST")["status"] == "API_ERROR"
