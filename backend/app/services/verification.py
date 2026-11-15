"""Interpretation of Valio price and availability responses into explicit verification states.

Price states:         VERIFIED | UNVERIFIED | UNAVAILABLE | API_ERROR | TIMEOUT
  VERIFIED     price present, error=false, currency present, quantity basis 1.0, validFrom/validTo strictly
               parseable, validFrom <= validTo, requested date inside [validFrom, validTo] (inclusive), and
               (if both are present) valueWithTax consistent with value and tax.taxRate.
  UNVERIFIED   a price was returned but one of the checks above fails (missing/malformed/inverted validity,
               expired or not-yet-valid, currency missing/mismatched, basis != 1, VAT inconsistent, value <= 0).
  UNAVAILABLE  the API answered but has no price for the product (error=true, catalogPrice null, product missing).
  API_ERROR / TIMEOUT   the call itself failed.

Availability states:  AVAILABLE | AVAILABLE_QTY_UNCONFIRMED | PARTIAL | LATE | UNAVAILABLE | UNKNOWN | API_ERROR | TIMEOUT
  Every schedule line is evaluated (not just the first). Lines are excluded when blocked, in an incompatible
  sales unit, or with an unreadable date; each line's classification is preserved as evidence together with
  the selected line. Quantities on several lines are NOT summed: the OpenAPI documents at most one line, so
  summing could overstate stock — the conservative choice is the largest single confirmed line.

Dates: strict ISO 8601 only (no fuzzy parsing). Timezone-aware values are converted to Europe/Helsinki
(the API's documented time zone) before taking the calendar date; naive values are taken as Helsinki time.

Trust states:         VERIFIED | UNKNOWN | ERROR  (for the Trust Layer)
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.integrations.valio_client import CallResult, ProductAvailability, ProductPrice

TERMINAL_PLACEHOLDER = 999999  # documented: "For Terminal products this is always 999999 since actual value is not known"

# quantity_basis values on an availability result
QTY_CONFIRMED = "CONFIRMED_STOCK"
QTY_TERMINAL = "TERMINAL_PLACEHOLDER"
QTY_STOCK_SENTINEL = "UNCONFIRMED_STOCK_SENTINEL"
# Explicit product decision for Junction 2026: Valio's Stock value 9999999999 is an unconfirmed supplier placeholder
# pending clarification; affected requests are routed for human consideration.
STOCK_SENTINEL_MESSAGE = "NOT CONFIRMED — supplier availability requires human review."


def fmt_qty(x: float | None) -> str:
    """Human-readable quantity that never uses scientific notation (9999999999 must not render as 1e+10)."""
    if x is None or not math.isfinite(x):
        return str(x)
    if float(x).is_integer():
        return str(int(x))
    return f"{x:.6f}".rstrip("0").rstrip(".")


def _stock_sentinels() -> set[float]:
    from app.config import get_settings

    return get_settings().stock_quantity_sentinel_values
HELSINKI = ZoneInfo("Europe/Helsinki")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:?\d{2})?$")
VAT_TOLERANCE = 0.011  # 1.1 % relative tolerance for rounding in valueWithTax


def parse_dt(value: str | None) -> datetime | None:
    """Strict ISO 8601 datetime/date parsing. Returns a Helsinki-local datetime, or None if unreadable."""
    if not value or not isinstance(value, str):
        return None
    v = value.strip()
    try:
        if _ISO_DATE.match(v):
            d = date.fromisoformat(v)
            return datetime(d.year, d.month, d.day, tzinfo=HELSINKI)
        if _ISO_DT.match(v):
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return dt.astimezone(HELSINKI) if dt.tzinfo else dt.replace(tzinfo=HELSINKI)
    except ValueError:
        return None
    return None


def parse_date(value: str | None) -> date | None:
    dt = parse_dt(value)
    return dt.date() if dt else None


def price_trust(status: str) -> str:
    return {"VERIFIED": "VERIFIED", "UNVERIFIED": "UNKNOWN", "UNAVAILABLE": "UNKNOWN"}.get(status, "ERROR")


def availability_trust(status: str) -> str:
    if status in ("AVAILABLE", "UNAVAILABLE", "LATE", "PARTIAL"):
        return "VERIFIED"
    if status in ("AVAILABLE_QTY_UNCONFIRMED", "UNKNOWN"):
        return "UNKNOWN"
    return "ERROR"


# ------------------------------------------------------------------ price
def interpret_price(call: CallResult, product_id: str, requested_date: date) -> dict:
    base = {"product_id": product_id, "source": call.source, "fetched_at": call.fetched_at,
            "simulated": call.simulated, "live": call.live, "pricing_date": requested_date.isoformat(),
            "checks": []}
    if call.status != "OK":
        status = "TIMEOUT" if call.status == "TIMEOUT" else "API_ERROR"
        return {**base, "status": status, "trust": "ERROR", "reason": call.error}
    pp: ProductPrice | None = call.items.get(product_id)
    if pp is None:
        return {**base, "status": "UNAVAILABLE", "trust": "UNKNOWN", "reason": "Product missing from price response"}
    cp = pp.catalogPrice
    out = {
        **base,
        "pricing_date": call.meta.get("pricingDate") or requested_date.isoformat(),
        "currency": (cp.currency if cp else None) or call.meta.get("currency"),
        "quantity_basis": pp.quantity,
        "unit_price_excl_tax": cp.value if cp else None,
        "unit_price_incl_tax": (cp.valueWithTax if cp else None) or (pp.priceWithTax.value if pp.priceWithTax else None),
        "tax_rate": pp.tax.taxRate if pp.tax else None,
        "tax_value": pp.tax.taxValue if pp.tax else None,
        "comparison_price": pp.comparisonPrice.value if pp.comparisonPrice else None,
        "comparison_unit": pp.comparisonUnit,
        "list_price": pp.listPrice.value if pp.listPrice else None,
        "net_price_found": pp.netPriceFound,
        "valid_from": cp.validFrom if cp else None,
        "valid_to": cp.validTo if cp else None,
        "price_basis": "per one sales unit (quantity 1.0), excl. VAT",
    }

    def fail(status: str, reason: str) -> dict:
        return {**out, "status": status, "trust": price_trust(status), "reason": reason}

    if pp.error:
        return fail("UNAVAILABLE", "Valio API reported error=true (no price found for this product)")
    if cp is None:
        return fail("UNAVAILABLE", "No catalogPrice in response")
    checks = out["checks"]
    if cp.value is None or cp.value <= 0:
        return fail("UNVERIFIED", f"Non-positive or missing price value ({cp.value})")
    checks.append("value > 0")
    top_cur = call.meta.get("currency")
    if not (cp.currency or "").strip():
        return fail("UNVERIFIED", "Price currency missing")
    if top_cur and cp.currency != top_cur:
        return fail("UNVERIFIED", f"Currency mismatch: product {cp.currency} vs response {top_cur}")
    checks.append(f"currency {cp.currency}")
    if pp.quantity is None or abs(pp.quantity - 1.0) > 1e-9:
        return fail("UNVERIFIED", f"Unexpected price quantity basis {pp.quantity} (documented as 1.0)")
    checks.append("quantity basis 1.0 sales unit")
    if not cp.validFrom or not cp.validTo:
        return fail("UNVERIFIED", "Price validity period missing (validFrom/validTo)")
    vf, vt = parse_date(cp.validFrom), parse_date(cp.validTo)
    if vf is None or vt is None:
        bad = cp.validFrom if vf is None else cp.validTo
        return fail("UNVERIFIED", f"Price validity date not in ISO 8601 format: {bad!r}")
    if vf > vt:
        return fail("UNVERIFIED", f"Invalid validity range: validFrom {vf} is after validTo {vt}")
    if requested_date < vf:
        return fail("UNVERIFIED", f"Price not yet valid on {requested_date} (valid from {vf})")
    if requested_date > vt:
        return fail("UNVERIFIED", f"Price expired: valid until {vt}, requested {requested_date}")
    out["valid_from_date"], out["valid_to_date"] = vf.isoformat(), vt.isoformat()
    checks.append(f"valid {vf}..{vt} covers {requested_date}")
    rate, incl = out["tax_rate"], out["unit_price_incl_tax"]
    if rate is not None and incl is not None:
        expected = cp.value * (1 + rate / 100)
        if abs(incl - expected) > VAT_TOLERANCE * expected + 0.005:
            return fail("UNVERIFIED", f"VAT inconsistent: valueWithTax {incl} vs value {cp.value} × (1 + {rate}%) = {expected:.4f}")
        checks.append(f"VAT {rate}% consistent with valueWithTax")
    elif rate is None:
        checks.append("VAT rate not provided by API (not inferred)")
    return {**out, "status": "VERIFIED", "trust": "VERIFIED",
            "reason": f"Price per sales unit confirmed by Valio API; valid {vf} → {vt}"}


# ------------------------------------------------------------------ availability
def interpret_availability(
    call: CallResult, product_id: str, requested_date: date, required_qty: float | None, catalogue_sales_unit: str
) -> dict:
    base = {"product_id": product_id, "source": call.source, "fetched_at": call.fetched_at,
            "simulated": call.simulated, "live": call.live, "requested_date": requested_date.isoformat(),
            "required_quantity": required_qty}
    if call.status != "OK":
        status = "TIMEOUT" if call.status == "TIMEOUT" else "API_ERROR"
        return {**base, "status": status, "trust": "ERROR", "reason": call.error}
    pa: ProductAvailability | None = call.items.get(product_id)
    if pa is None:
        return {**base, "status": "UNKNOWN", "trust": "UNKNOWN", "reason": "Product missing from availability response"}

    time_now = parse_dt(call.meta.get("timeNow"))
    closing = parse_dt(pa.closingTime)
    terminal_model = (pa.logisticModel or "").strip().lower().startswith("terminal")
    sentinels = _stock_sentinels()
    out: dict[str, Any] = {
        **base,
        "logistic_model": pa.logisticModel,
        "sales_block": pa.salesBlock,
        "closing_time": pa.closingTime,
        "cutoff_passed": bool(closing and time_now and closing < time_now),
        "time_now": call.meta.get("timeNow"),
        "lines_total": len(pa.scheduleLines),
        "lines": [],
        "selected_line": None,
        "available_date": None,
        "raw_available_amount": None,  # exactly what the supplier sent on the selected line (evidence)
        "available_amount": None,  # only set when the quantity is CONFIRMED stock
        "quantity_basis": None,  # CONFIRMED_STOCK | TERMINAL_PLACEHOLDER | UNCONFIRMED_STOCK_SENTINEL
        "sales_unit": None,
    }

    def done(status: str, reason: str, line: dict | None = None) -> dict:
        if line:
            out["selected_line"] = line
            out["available_date"] = line["available_date"]
            out["raw_available_amount"] = line["available_amount"]
            out["available_amount"] = None if line["quantity_unknown"] else line["available_amount"]
            out["quantity_basis"] = line["quantity_basis"]
            out["sales_unit"] = line["sales_unit"]
        return {**out, "status": status, "trust": availability_trust(status), "reason": reason}

    if pa.salesBlock:
        return done("UNAVAILABLE", "Sales block: product cannot currently be ordered")
    if not pa.scheduleLines:
        return done("UNAVAILABLE", "No schedule lines: product cannot currently be delivered on any date")

    usable: list[dict] = []
    for i, ln in enumerate(pa.scheduleLines):
        d = parse_date(ln.availableDate)
        amount = ln.availableAmount
        malformed = amount is None or not math.isfinite(amount) or amount < 0
        terminal = terminal_model or amount == TERMINAL_PLACEHOLDER
        stock_sentinel = not terminal and not malformed and amount in sentinels
        basis = QTY_TERMINAL if terminal else QTY_STOCK_SENTINEL if stock_sentinel else QTY_CONFIRMED
        info = {"index": i, "available_date": ln.availableDate, "parsed_date": d.isoformat() if d else None,
                "available_amount": amount, "sales_unit": ln.salesUnit, "sales_block": ln.salesBlock,
                "terminal": terminal, "stock_sentinel": stock_sentinel, "quantity_basis": basis,
                "quantity_unknown": terminal or stock_sentinel}
        if ln.salesBlock:
            info["classification"] = "EXCLUDED_SALES_BLOCK"
        elif malformed and not terminal:
            info["classification"] = "EXCLUDED_MALFORMED_AMOUNT"
        elif ln.salesUnit and catalogue_sales_unit and ln.salesUnit != catalogue_sales_unit:
            info["classification"] = f"EXCLUDED_UNIT_MISMATCH ({ln.salesUnit} ≠ {catalogue_sales_unit})"
        elif d is None:
            info["classification"] = "EXCLUDED_UNREADABLE_DATE"
        elif not info["quantity_unknown"] and amount == 0:
            info["classification"] = "ZERO_QUANTITY"
        else:
            info["on_time"] = d <= requested_date
            info["classification"] = "ON_TIME" if info["on_time"] else "LATE"
            usable.append(info)
        out["lines"].append(info)

    classes = [ln["classification"] for ln in out["lines"]]
    if not usable:
        if all(c == "EXCLUDED_SALES_BLOCK" for c in classes):
            return done("UNAVAILABLE", "All schedule lines are sales-blocked")
        if any(c == "ZERO_QUANTITY" for c in classes) and all(c in ("ZERO_QUANTITY", "EXCLUDED_SALES_BLOCK") for c in classes):
            return done("UNAVAILABLE", "Available amount is 0 on every schedule line")
        if any(c == "EXCLUDED_MALFORMED_AMOUNT" for c in classes):
            return done("UNKNOWN", "Available amount in the supplier response is malformed (negative or not a finite number); "
                                   "never treated as available")
        if any(c.startswith("EXCLUDED_UNIT_MISMATCH") for c in classes):
            return done("UNKNOWN", f"Availability sales unit differs from catalogue sales unit {catalogue_sales_unit}; "
                                   "quantity cannot be compared")
        return done("UNKNOWN", "No schedule line has a readable delivery date")

    on_time = [ln for ln in usable if ln["on_time"]]
    later = sorted([ln for ln in usable if not ln["on_time"]], key=lambda ln: ln["parsed_date"])
    on_time_stock = sorted([ln for ln in on_time if not ln["quantity_unknown"]], key=lambda ln: (ln["parsed_date"], -ln["available_amount"]))
    on_time_terminal = [ln for ln in on_time if ln["quantity_unknown"]]
    multi = f" ({len(pa.scheduleLines)} schedule lines evaluated)" if len(pa.scheduleLines) > 1 else ""

    if required_qty is not None:
        full = [ln for ln in on_time_stock if ln["available_amount"] >= required_qty]
        if full:
            ln = full[0]
            return done("AVAILABLE", f"{fmt_qty(ln['available_amount'])} {ln['sales_unit']} available on {ln['parsed_date']} (Stock){multi}", ln)
    if on_time_terminal:
        ln = sorted(on_time_terminal, key=lambda x: x["parsed_date"])[0]
        if ln["stock_sentinel"]:
            # Product decision (Junction 2026): the raw sentinel is kept in evidence only, never shown as inventory.
            return done("AVAILABLE_QTY_UNCONFIRMED",
                        f"{STOCK_SENTINEL_MESSAGE} Deliverable on {ln['parsed_date']}; the Stock quantity reported by Valio "
                        f"is an unconfirmed supplier placeholder{multi}", ln)
        return done("AVAILABLE_QTY_UNCONFIRMED",
                    f"Deliverable on {ln['parsed_date']}, but quantity is unknown (Terminal / 999999 placeholder, not stock){multi}", ln)
    if on_time_stock and required_qty is None:
        ln = max(on_time_stock, key=lambda x: x["available_amount"])
        return done("AVAILABLE_QTY_UNCONFIRMED", f"{fmt_qty(ln['available_amount'])} {ln['sales_unit']} available; required quantity unknown{multi}", ln)
    later_full = [ln for ln in later if ln["quantity_unknown"] or required_qty is None or ln["available_amount"] >= required_qty]
    if on_time_stock:
        best = max(on_time_stock, key=lambda x: x["available_amount"])
        if later_full:
            ln = later_full[0]
            return done("LATE", f"Only {fmt_qty(best['available_amount'])} of {fmt_qty(required_qty)} {best['sales_unit']} by the requested date; "
                                f"full quantity earliest {ln['parsed_date']}{multi}", ln)
        return done("PARTIAL", f"Only {fmt_qty(best['available_amount'])} of {fmt_qty(required_qty)} {best['sales_unit']} available by the date "
                               f"(quantities on separate lines are not summed){multi}", best)
    if later_full:
        ln = later_full[0]
        return done("LATE", f"Earliest delivery {ln['parsed_date']} is after requested {requested_date.isoformat()}{multi}", ln)
    ln = max(later, key=lambda x: x["available_amount"])
    return done("PARTIAL", f"Only {fmt_qty(ln['available_amount'])} of {fmt_qty(required_qty)} {ln['sales_unit']} available, and only from "
                           f"{ln['parsed_date']}{multi}", ln)


def evidence_item(eid: str, claim: str, value: Any, trust: str, source: str | None, product_id: str | None,
                  timestamp: str | None, detail: str | None = None) -> dict:
    return {"id": eid, "claim": claim, "value": value, "state": trust, "source": source,
            "product_id": product_id, "timestamp": timestamp, "detail": detail}
