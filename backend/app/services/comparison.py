"""Side-by-side comparison of the top eligible recommendations.

Highlights are computed only from evidence that exists. Rules:
- A criterion gets a "best" only when at least two compared products have a value for it.
  Otherwise the result is INSUFFICIENT_EVIDENCE, and no winner label is given.
- When several products share the best value, all of them are listed and marked as a tie.
- Price comparisons use VERIFIED totals for the same requested quantity only.
- Reliability is historical performance, not a delivery guarantee.
- Carbon values are product-group averages. Identical group averages give no sustainability winner.
"""
from __future__ import annotations

from typing import Any

EPS = 1e-9


def _best(values: dict[str, float | None], lower_better: bool, criterion: str, basis: str) -> dict:
    present = {k: v for k, v in values.items() if v is not None}
    out: dict[str, Any] = {"criterion": criterion, "basis": basis, "values": values, "best": [], "tie": False}
    if len(present) < 2:
        out["status"] = "INSUFFICIENT_EVIDENCE"
        out["note"] = (f"Only {len(present)} of {len(values)} products have this evidence; no comparison winner is declared."
                       if values else "No products to compare.")
        return out
    target = min(present.values()) if lower_better else max(present.values())
    best = [k for k, v in present.items() if abs(v - target) < EPS]
    out["best"] = best
    if len(best) == len(present):
        out["status"] = "NO_DIFFERENCE"
        out["tie"] = True
        out["note"] = "All compared products have the same value; no winner."
        out["best"] = []
    else:
        out["status"] = "COMPARED"
        out["tie"] = len(best) > 1
        missing = [k for k, v in values.items() if v is None]
        out["note"] = (f"Compared across {len(present)} products with evidence"
                       + (f"; {', '.join(missing)} not comparable (missing evidence)" if missing else ""))
    return out


def build_comparison(cands: list[dict]) -> dict:
    top = cands[:3]
    if not top:
        return {"products": [], "criteria": [], "note": "No eligible products to compare."}
    ids = [c["id"] for c in top]

    def price_total(c):
        p = c.get("price") or {}
        return c.get("total_excl_tax") if p.get("status") == "VERIFIED" else None

    price = _best({c["id"]: price_total(c) for c in top}, True, "Best verified price",
                  "Lowest VERIFIED total excl. VAT for the requested quantity (Valio API POST /api/price)")
    # Totals are only like-for-like when every product delivers the same amount from a verified conversion
    priced = [c for c in top if price_total(c) is not None]
    deliverables = {c["id"]: ((c.get("quantity") or {}).get("deliverable_amount"), (c.get("conversion") or {}).get("status")) for c in priced}
    caveats = []
    amounts = {round(v[0], 3) for v in deliverables.values() if v[0] is not None}
    if len(amounts) > 1:
        caveats.append("Totals cover different deliverable quantities ("
                       + ", ".join(f"{pid}: {v[0]:g}" for pid, v in deliverables.items() if v[0] is not None)
                       + ") because of pack and lot sizes; compare per-unit cost as well.")
    est = [pid for pid, v in deliverables.items() if v[1] != "VERIFIED"]
    if est:
        caveats.append(f"Quantity conversion not verified for {', '.join(est)}; their totals may not match the request.")
    price["caveats"] = caveats
    if caveats and price["status"] == "COMPARED":
        price["note"] += ". Caveats: " + " ".join(caveats)

    rel = _best({c["id"]: c.get("reliability") for c in top}, False, "Best delivery reliability",
                "Highest HISTORICAL delivery reliability (CSV). Past performance — not a delivery guarantee")
    carbon = _best({c["id"]: c.get("carbon_kg_per_kg") for c in top}, True, "Best sustainability evidence",
                   "Lowest kg CO2e/kg — product-group average estimate, not a product-specific measurement")

    rows = []
    for c in top:
        conv = c.get("conversion") or {}
        alg = c.get("allergens") or {}
        av = c.get("availability") or {}
        pr = c.get("price") or {}
        uncertainties = []
        if pr.get("status") != "VERIFIED":
            uncertainties.append(f"Price {pr.get('status', 'UNKNOWN')}")
        if av.get("status") != "AVAILABLE":
            uncertainties.append(f"Availability {av.get('status', 'UNKNOWN')}")
        if conv.get("status") != "VERIFIED":
            uncertainties.append(f"Quantity conversion {conv.get('status', 'UNKNOWN')}")
        if alg.get("overall") == "UNKNOWN":
            uncertainties.append("Allergen exclusion not verifiable")
        if c.get("reliability") is None:
            uncertainties.append("No reliability record")
        if c.get("carbon_kg_per_kg") is None:
            uncertainties.append("No carbon record")
        for cl in c.get("claims") or []:
            if cl.get("status") != "VERIFIED":
                uncertainties.append(f"{cl.get('claim')} {cl.get('status')}")
        q = c.get("quantity") or {}
        rows.append({
            "product_id": c["id"],
            "name": (c.get("product") or {}).get("name"),
            "rank": c.get("rank"),
            "policy_decision": (c.get("policy") or {}).get("decision"),
            "quantity_compatibility": {
                "status": conv.get("status"),
                "requested": q.get("requested_amount"), "requested_unit": q.get("requested_unit"),
                "order_quantity": q.get("order_quantity"), "sales_unit": q.get("sales_unit"),
                "deliverable": q.get("deliverable_amount"), "deliverable_unit": q.get("deliverable_unit"),
                "surplus": q.get("surplus"),
            },
            "allergen_compliance": alg.get("overall"),
            "allergen_checks": [{"code": a["code"], "status": a["status"]} for a in alg.get("checks", [])],
            "uncertainties": uncertainties,
        })
    return {
        "products": ids,
        "criteria": [price, rel, carbon],
        "rows": rows,
        "note": "Highlights only where evidence exists for at least two products; ties and missing evidence are shown, never resolved by guessing.",
    }
