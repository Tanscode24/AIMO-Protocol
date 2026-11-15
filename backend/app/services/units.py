"""Unit and quantity intelligence.

Converts a requested physical amount (litres / kg) into product sales units using only
catalogue data: salesUnit, baseUnit, units[].sizeInBaseUnits, synkkaData.unitConversions,
and allowedLotSize. The product name is used only as a *cross-check*.

Conversion states
  VERIFIED   declared at the sales-unit packaging level (and not contradicted by the name),
             or derived from the base unit AND confirmed by the name
  ESTIMATED  only derivable from packaging hierarchy or name; not independently confirmed
  CONFLICT   declared/derived value contradicts the product name -> unusable
  UNKNOWN    no usable information -> unusable

Observed in the dataset: BASE_UNIT_OR_EACH describes the base unit, CASE describes the
sales unit when salesUnit != baseUnit. Some records conflict (e.g. "Valio butter 4x5 kg"
declares 20 kg at BASE_UNIT_OR_EACH while 4 base units make up a KI), so name cross-checks matter.
"""
from __future__ import annotations

import json
import math
import re
from decimal import ROUND_CEILING, Decimal
from typing import Any

VOLUME_TO_L = {"LTR": 1.0, "MLT": 0.001, "CLT": 0.01, "DLT": 0.1}
WEIGHT_TO_KG = {"KGM": 1.0, "GRM": 0.001}
NAME_UNITS = {"l": ("volume", 1.0), "dl": ("volume", 0.1), "cl": ("volume", 0.01), "ml": ("volume", 0.001),
              "kg": ("weight", 1.0), "g": ("weight", 0.001)}
# Units that are weighed (fractional quantities allowed down to the lot size)
WEIGHT_SALES_UNITS = {"KG"}
TOLERANCE = 0.03

_NUM = r"(\d+(?:[.,]\d+)?)"
_U = r"(kg|g|l|dl|cl|ml)"
RE_COUNT_FIRST = re.compile(rf"(\d+)\s*(?:pcs|pc|kpl|st|pieces|pack)?\s*[x×]\s*{_NUM}\s*{_U}\b", re.I)
RE_AMOUNT_FIRST = re.compile(rf"{_NUM}\s*{_U}\s*[x×]\s*(\d+)\b", re.I)
RE_SINGLE = re.compile(rf"(?<![\d.,]){_NUM}\s*{_U}\b(?!\s*%)", re.I)
RE_MULTIPACK_PREFIX = re.compile(r"^\s*(\d+)\s*[x×]\s*[^\d\s]", re.I)


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def parse_name_amount(name: str | None) -> dict | None:
    """Extract pack size from a product name, e.g. '12 x 1 L' or '400g'."""
    if not name:
        return None
    m = RE_COUNT_FIRST.search(name)
    if m:
        count, amount, unit = int(m.group(1)), _num(m.group(2)), m.group(3).lower()
    else:
        m = RE_AMOUNT_FIRST.search(name)
        if m:
            amount, unit, count = _num(m.group(1)), m.group(2).lower(), int(m.group(3))
        else:
            m = RE_SINGLE.search(name)
            if not m:
                return None
            amount, unit, count = _num(m.group(1)), m.group(2).lower(), 1
            prefix = RE_MULTIPACK_PREFIX.match(name)  # e.g. "12 x Arla Whole milk 1 L"
            if prefix:
                count = int(prefix.group(1))
    dim, factor = NAME_UNITS[unit]
    if amount <= 0:
        return None
    return {"count": count, "amount": amount * factor, "dimension": dim, "text": m.group(0)}


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= TOLERANCE * max(abs(a), abs(b), 1e-9)


def _level_value(conversions: list[dict], level: str, dim: str) -> float | None:
    for c in conversions:
        if c.get("synkkaPackagingType") != level:
            continue
        if dim == "volume" and isinstance(c.get("volume"), dict):
            f = VOLUME_TO_L.get(str(c["volume"].get("unit", "")).strip())
            if f and c["volume"].get("value"):
                return float(c["volume"]["value"]) * f
        if dim == "weight" and isinstance(c.get("netWeight"), dict):
            f = WEIGHT_TO_KG.get(str(c["netWeight"].get("unit", "")).strip())
            if f and c["netWeight"].get("value"):
                return float(c["netWeight"]["value"]) * f
    return None


def sales_unit_size(product: Any) -> float | None:
    for u in json.loads(product.units_json or "[]"):
        if u.get("unitId") == product.sales_unit:
            return float(u.get("sizeInBaseUnits") or 0) or None
    return None


def per_sales_unit(product: Any, dim: str) -> dict:
    """Amount (litres for volume, kg for weight) contained in ONE sales unit."""
    conversions = json.loads(product.unit_conversions_json or "[]")
    size = sales_unit_size(product)
    same = product.sales_unit == product.base_unit
    unit_word = "L" if dim == "volume" else "kg"

    # Products sold by weight: 1 KG sales unit = 1 kg by definition
    if dim == "weight" and product.sales_unit == "KG":
        return {"status": "VERIFIED", "value": 1.0, "unit": unit_word, "source": "salesUnit KG", "notes": []}

    declared, declared_src = None, None
    base_val = _level_value(conversions, "BASE_UNIT_OR_EACH", dim)
    if same:
        declared, declared_src = base_val, "unitConversions BASE_UNIT_OR_EACH (salesUnit = baseUnit)"
    else:
        case_val = _level_value(conversions, "CASE", dim)
        if case_val:
            declared, declared_src = case_val, "unitConversions CASE (sales unit level)"
    derived = base_val * size if (not same and base_val and size) else None

    name_val, name_info = None, None
    for nm in (product.name_en, product.name_fi):
        info = parse_name_amount(nm)
        if info and info["dimension"] == dim:
            name_info = info
            if info["count"] > 1:
                if same or (size and int(size) == info["count"]):
                    name_val = info["count"] * info["amount"]
                else:
                    name_val = None  # multipack count does not match packaging hierarchy
            else:
                name_val = info["amount"] * (size if (not same and size) else 1)
            break

    notes: list[str] = []
    if name_info:
        notes.append(f"Name states '{name_info['text']}'")
    if declared:
        if name_val and not _close(declared, name_val):
            return {"status": "CONFLICT", "value": None, "unit": unit_word, "source": declared_src,
                    "notes": notes + [f"Declared {declared:g} {unit_word} conflicts with name-derived {name_val:g} {unit_word}"]}
        if name_val:
            notes.append("Confirmed by product name")
        return {"status": "VERIFIED", "value": declared, "unit": unit_word, "source": declared_src, "notes": notes}
    if derived:
        if name_val and _close(derived, name_val):
            return {"status": "VERIFIED", "value": derived, "unit": unit_word,
                    "source": f"BASE_UNIT_OR_EACH x {size:g} {product.base_unit} per {product.sales_unit}, confirmed by name",
                    "notes": notes}
        if name_val:
            return {"status": "CONFLICT", "value": None, "unit": unit_word, "source": "packaging hierarchy",
                    "notes": notes + [f"Derived {derived:g} {unit_word} conflicts with name-derived {name_val:g} {unit_word}"]}
        return {"status": "ESTIMATED", "value": derived, "unit": unit_word,
                "source": f"BASE_UNIT_OR_EACH x {size:g} {product.base_unit} (not independently confirmed)", "notes": notes}
    if name_val:
        return {"status": "ESTIMATED", "value": name_val, "unit": unit_word,
                "source": "product name only (no declared packaging measure)", "notes": notes}
    return {"status": "UNKNOWN", "value": None, "unit": unit_word, "source": None,
            "notes": notes + ["No volume/weight data for the sales unit"]}


def round_to_lot(raw_quantity: float, lot_size: float, sales_unit: str) -> float:
    """Round UP to a valid order quantity: a multiple of allowedLotSize, and a whole number for
    count-based sales units (a fraction of a carton cannot be delivered)."""
    step = Decimal(str(lot_size)) if lot_size and lot_size > 0 else Decimal("0.01")
    if sales_unit not in WEIGHT_SALES_UNITS:
        step = max(step, Decimal(1)) if step < 1 else step
        if step != step.to_integral_value():
            step = Decimal(math.ceil(step))
    q = Decimal(str(raw_quantity))
    n = (q / step).to_integral_value(rounding=ROUND_CEILING)
    return float(n * step)


def is_valid_lot(quantity: float, lot_size: float) -> bool:
    if not lot_size or lot_size <= 0:
        return True
    q, step = Decimal(str(quantity)), Decimal(str(lot_size))
    return (q % step) == 0


def convert_request(product: Any, quantity: float | None, unit: str | None) -> dict:
    """Convert the requested quantity into sales units for this product."""
    base = {"sales_unit": product.sales_unit, "base_unit": product.base_unit,
            "allowed_lot_size": product.allowed_lot_size, "sales_unit_size_in_base_units": sales_unit_size(product)}
    if quantity is None or quantity <= 0:
        return {**base, "status": "UNKNOWN", "quantity_sales_units": None,
                "notes": ["No quantity in request"], "per_sales_unit": None}
    if unit in (None, "SALES_UNIT"):
        q = round_to_lot(quantity, product.allowed_lot_size, product.sales_unit)
        notes = [f"Quantity interpreted as {product.sales_unit} sales units"]
        if q != quantity:
            notes.append(f"Rounded up from {quantity:g} to {q:g} to satisfy lot size {product.allowed_lot_size:g}")
        return {**base, "status": "VERIFIED", "quantity_sales_units": q, "requested": quantity,
                "requested_unit": "sales units", "per_sales_unit": None, "notes": notes}
    dim = "volume" if unit == "L" else "weight"
    per = per_sales_unit(product, dim)
    out = {**base, "per_sales_unit": per, "requested": quantity, "requested_unit": unit}
    if per["status"] in ("CONFLICT", "UNKNOWN") or not per["value"]:
        return {**out, "status": per["status"], "quantity_sales_units": None,
                "notes": per["notes"] + [f"Cannot verify how many {product.sales_unit} make {quantity:g} {unit}"]}
    raw = quantity / per["value"]
    q = round_to_lot(raw, product.allowed_lot_size, product.sales_unit)
    delivered = q * per["value"]
    notes = list(per["notes"])
    notes.append(f"{quantity:g} {unit} / {per['value']:g} {per['unit']} per {product.sales_unit} = {raw:.2f} -> order {q:g} {product.sales_unit}")
    if delivered - quantity > 1e-9:
        notes.append(f"Delivers {delivered:g} {per['unit']} ({delivered - quantity:g} above request due to pack/lot size)")
    return {**out, "status": per["status"], "quantity_sales_units": q, "delivered_amount": round(delivered, 4), "notes": notes}


def net_weight_kg_per_sales_unit(product: Any) -> dict:
    return per_sales_unit(product, "weight")
