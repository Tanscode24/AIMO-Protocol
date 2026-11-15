"""P0 regression tests: all schedule lines are evaluated; price validity is strict."""
from datetime import date

import pytest

from app.integrations.valio_client import CallResult, ProductAvailability, ProductPrice
from app.services.verification import interpret_availability, interpret_price, parse_date

D = date(2026, 11, 16)  # requested date


def avail(lines, model="Stock", block=False, time_now="2026-11-14T10:00:00", closing="2026-11-15T10:00:00"):
    pa = {"productId": "1", "closingTime": closing, "salesBlock": block, "logisticModel": model,
          "scheduleLines": [{"salesBlock": l.get("block", False), "availableAmount": l["amount"],
                             "availableDate": l.get("date"), "salesUnit": l.get("unit", "ST")} for l in lines]}
    return CallResult("availability", "OK", items={"1": ProductAvailability.model_validate(pa)}, meta={"timeNow": time_now})


def st(lines, qty=50, **kw):
    return interpret_availability(avail(lines, **kw), "1", D, qty, "ST")


# ---------------------------------------------------------------- availability: multiple lines
def test_second_line_satisfies_when_first_is_late():
    r = st([{"amount": 100, "date": "2026-11-20"}, {"amount": 80, "date": "2026-11-16"}])
    assert r["status"] == "AVAILABLE"  # old code looked only at line[0] and reported LATE
    assert r["selected_line"]["index"] == 1 and r["available_date"] == "2026-11-16"
    assert len(r["lines"]) == 2 and r["lines"][0]["classification"] == "LATE"


def test_first_line_blocked_second_ok():
    r = st([{"amount": 100, "date": "2026-11-16", "block": True}, {"amount": 60, "date": "2026-11-15"}])
    assert r["status"] == "AVAILABLE" and r["selected_line"]["index"] == 1
    assert r["lines"][0]["classification"] == "EXCLUDED_SALES_BLOCK"


def test_all_lines_blocked_is_unavailable():
    r = st([{"amount": 100, "date": "2026-11-16", "block": True}, {"amount": 100, "date": "2026-11-17", "block": True}])
    assert r["status"] == "UNAVAILABLE"


def test_partial_on_time_full_later_is_late_with_full_date():
    r = st([{"amount": 20, "date": "2026-11-16"}, {"amount": 100, "date": "2026-11-19"}])
    assert r["status"] == "LATE" and r["available_date"] == "2026-11-19"
    assert "Only 20 of 50" in r["reason"]


def test_partial_everywhere_is_partial_and_lines_not_summed():
    r = st([{"amount": 30, "date": "2026-11-15"}, {"amount": 30, "date": "2026-11-16"}])
    assert r["status"] == "PARTIAL"  # 30 + 30 would be 60 >= 50, but quantities are never summed
    assert r["available_amount"] == 30


def test_only_late_lines():
    r = st([{"amount": 100, "date": "2026-11-21"}, {"amount": 100, "date": "2026-11-18"}])
    assert r["status"] == "LATE" and r["available_date"] == "2026-11-18"  # earliest full line


def test_zero_on_all_lines_unavailable():
    assert st([{"amount": 0, "date": "2026-11-16"}, {"amount": 0, "date": "2026-11-15"}])["status"] == "UNAVAILABLE"


def test_incompatible_unit_line_ignored_but_compatible_line_used():
    r = st([{"amount": 500, "date": "2026-11-16", "unit": "KI"}, {"amount": 60, "date": "2026-11-16", "unit": "ST"}])
    assert r["status"] == "AVAILABLE" and r["sales_unit"] == "ST"
    assert r["lines"][0]["classification"].startswith("EXCLUDED_UNIT_MISMATCH")


def test_only_incompatible_units_is_unknown():
    r = st([{"amount": 500, "date": "2026-11-16", "unit": "KI"}])
    assert r["status"] == "UNKNOWN"


def test_terminal_line_is_quantity_unconfirmed_never_stock():
    r = st([{"amount": 999999, "date": "2026-11-16"}], model="Terminal")
    assert r["status"] == "AVAILABLE_QTY_UNCONFIRMED" and r["available_amount"] is None and r["trust"] == "UNKNOWN"


def test_stock_line_preferred_over_terminal_placeholder():
    r = st([{"amount": 999999, "date": "2026-11-15"}, {"amount": 100, "date": "2026-11-16"}])
    assert r["status"] == "AVAILABLE" and r["available_amount"] == 100


def test_malformed_dates():
    r = st([{"amount": 100, "date": "next tuesday"}, {"amount": 100, "date": None}])
    assert r["status"] == "UNKNOWN"
    r2 = st([{"amount": 100, "date": "16.11.2026"}, {"amount": 100, "date": "2026-11-16"}])
    assert r2["status"] == "AVAILABLE" and r2["lines"][0]["classification"] == "EXCLUDED_UNREADABLE_DATE"


def test_timezone_conversion_to_helsinki():
    # 2026-11-16T23:30Z is already 17 Nov in Helsinki (UTC+2) -> late for a 16 Nov request
    assert st([{"amount": 100, "date": "2026-11-16T23:30:00Z"}])["status"] == "LATE"
    # 2026-11-15T23:30Z is 16 Nov 01:30 in Helsinki -> on time
    assert st([{"amount": 100, "date": "2026-11-15T23:30:00Z"}])["status"] == "AVAILABLE"


def test_cutoff_with_mixed_timezones():
    r = st([{"amount": 100, "date": "2026-11-16"}], time_now="2026-11-15T12:00:00+02:00", closing="2026-11-15T09:00:00Z")
    assert r["cutoff_passed"] is True  # 09:00Z = 11:00 Helsinki < 12:00 Helsinki


def test_unknown_required_quantity_never_confirmed():
    r = interpret_availability(avail([{"amount": 5, "date": "2026-11-16"}]), "1", D, None, "ST")
    assert r["status"] == "AVAILABLE_QTY_UNCONFIRMED"


def test_product_level_sales_block_and_empty_schedule():
    assert st([{"amount": 100, "date": "2026-11-16"}], block=True)["status"] == "UNAVAILABLE"
    assert st([])["status"] == "UNAVAILABLE"


# ---------------------------------------------------------------- price validity
def price(**cp_over):
    cp = {"value": 2.0, "valueWithTax": 2.27, "validFrom": "2026-11-01", "validTo": "2026-11-30", "currency": "EUR"}
    cp.update(cp_over)
    pp = {"productId": "1", "quantity": 1.0, "netPriceFound": False, "error": False, "catalogPrice": cp,
          "tax": {"taxRate": 13.5, "taxValue": 0.27}}
    return CallResult("price", "OK", items={"1": ProductPrice.model_validate(pp)}, meta={"currency": "EUR"})


@pytest.mark.parametrize("vf,vt,reason", [
    ("garbage", "2026-11-30", "ISO 8601"),
    ("2026-11-01", "Tuesday", "ISO 8601"),       # old lenient parser accepted words like this
    ("2026-13-01", "2026-11-30", "ISO 8601"),   # impossible month
    ("01.11.2026", "30.11.2026", "ISO 8601"),
    ("", "2026-11-30", "missing"),
    ("2026-11-30", "2026-11-01", "Invalid validity range"),
])
def test_malformed_missing_or_inverted_validity_never_verified(vf, vt, reason):
    r = interpret_price(price(validFrom=vf, validTo=vt), "1", D)
    assert r["status"] == "UNVERIFIED" and reason in r["reason"]


def test_expired_price():
    r = interpret_price(price(validFrom="2026-10-01", validTo="2026-11-15"), "1", D)
    assert r["status"] == "UNVERIFIED" and "expired" in r["reason"]


def test_future_valid_price():
    r = interpret_price(price(validFrom="2026-11-17", validTo="2026-12-31"), "1", D)
    assert r["status"] == "UNVERIFIED" and "not yet valid" in r["reason"]


@pytest.mark.parametrize("vf,vt", [("2026-11-16", "2026-11-30"), ("2026-11-01", "2026-11-16"), ("2026-11-16", "2026-11-16"),
                                   ("2026-11-16T00:00:00", "2026-11-16T23:59:59"), ("2026-11-15T22:00:00Z", "2026-11-16T21:00:00Z")])
def test_boundary_dates_are_inclusive(vf, vt):
    r = interpret_price(price(validFrom=vf, validTo=vt), "1", D)
    assert r["status"] == "VERIFIED", r["reason"]


def test_vat_semantics_preserved_and_inconsistency_rejected():
    ok = interpret_price(price(), "1", D)
    assert ok["unit_price_excl_tax"] == 2.0 and ok["unit_price_incl_tax"] == 2.27 and ok["tax_rate"] == 13.5
    bad = interpret_price(price(valueWithTax=3.50), "1", D)
    assert bad["status"] == "UNVERIFIED" and "VAT inconsistent" in bad["reason"]


def test_currency_and_value_checks():
    assert interpret_price(price(currency=""), "1", D)["status"] == "UNVERIFIED"
    assert interpret_price(price(currency="SEK"), "1", D)["status"] == "UNVERIFIED"
    assert interpret_price(price(value=0.0, valueWithTax=0.0), "1", D)["status"] == "UNVERIFIED"


def test_api_errors_distinct_from_unavailable_and_unverified():
    assert interpret_price(CallResult("price", "API_ERROR", error="x"), "1", D)["status"] == "API_ERROR"
    assert interpret_price(CallResult("price", "TIMEOUT", error="x"), "1", D)["status"] == "TIMEOUT"
    assert interpret_price(CallResult("price", "OK"), "1", D)["status"] == "UNAVAILABLE"


def test_strict_date_parser():
    assert parse_date("2026-11-16") == date(2026, 11, 16)
    for bad in ("Monday", "16/11/2026", "2026-11-16 junk", "", None, "2026-02-30"):
        assert parse_date(bad) is None
