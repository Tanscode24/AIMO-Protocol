"""Deterministic, explainable ranking.

score = sum(w_f * s_f) / sum(w_f) over the factors that are AVAILABLE for that candidate,
where s_f is min-max normalised across the eligible candidate set:
  price          lower total for the requested quantity is better (VERIFIED prices only)
  reliability    higher historical delivery reliability is better (CSV, product-specific)
  sustainability lower kg CO2e/kg is better (CSV, product-group average)
  match          request/catalogue match score
A missing factor is removed and the remaining weights are renormalised. Missing values are
never treated as zero. Ranking never overrides hard constraints (applied before ranking).
"""
from __future__ import annotations

FACTORS = ("price", "reliability", "sustainability", "match")
LOWER_IS_BETTER = {"price", "sustainability"}


def weights_from_policy(policy, priorities: list[str] | None = None) -> dict[str, float]:
    w = {
        "price": policy.weight_price,
        "reliability": policy.weight_reliability,
        "sustainability": policy.weight_sustainability,
        "match": policy.weight_match,
    }
    for p in priorities or []:
        if p in w:
            w[p] *= 1.5  # explicit user priority boosts that factor
    total = sum(w.values()) or 1.0
    return {k: round(v / total, 4) for k, v in w.items()}


def _norm(values: dict[str, float], lower_better: bool) -> dict[str, float]:
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi - lo < 1e-12:
        return {k: 1.0 for k in values}
    return {k: ((hi - v) if lower_better else (v - lo)) / (hi - lo) for k, v in values.items()}


def rank(candidates: list[dict], weights: dict[str, float]) -> list[dict]:
    """candidates: dicts with keys id and metrics {price, reliability, sustainability, match} (None = missing)."""
    normed = {}
    for f in FACTORS:
        vals = {c["id"]: c["metrics"][f] for c in candidates if c["metrics"].get(f) is not None}
        normed[f] = _norm(vals, f in LOWER_IS_BETTER)
    out = []
    for c in candidates:
        present = [f for f in FACTORS if c["id"] in normed[f]]
        missing = [f for f in FACTORS if f not in present]
        wsum = sum(weights[f] for f in present)
        factor_scores = {f: round(normed[f][c["id"]], 4) for f in present}
        effective = {f: round(weights[f] / wsum, 4) for f in present} if wsum else {}
        score = min(1.0, sum(factor_scores[f] * effective[f] for f in present)) if wsum else 0.0
        # Transparent breakdown: contribution (score points out of 100) per factor. Missing factors contribute
        # nothing and are listed explicitly; their weight is redistributed (never scored as zero).
        breakdown = []
        for f in FACTORS:
            raw = c["metrics"].get(f)
            row = {"factor": f, "raw_value": raw, "base_weight": round(weights[f], 4),
                   "status": "PRESENT" if f in present else "MISSING"}
            if f in present:
                row.update({"normalised": factor_scores[f], "effective_weight": effective[f],
                            "contribution": round(factor_scores[f] * effective[f] * 100, 2)})
            else:
                row.update({"normalised": None, "effective_weight": 0.0, "contribution": None,
                            "redistributed_weight": round(weights[f], 4)})
            breakdown.append(row)
        coverage = round(sum(weights[f] for f in present), 4)
        out.append({**c, "score": round(score, 4), "factor_scores": factor_scores,
                    "effective_weights": effective, "base_weights": dict(weights), "missing_factors": missing,
                    "score_breakdown": breakdown, "evidence_coverage": coverage,
                    "comparison_set_size": len(candidates),
                    "score_note": _score_note(coverage, missing, len(candidates))})
    return out


FACTOR_NAMES = {"price": "verified price", "reliability": "delivery reliability", "sustainability": "carbon estimate",
                "match": "request match"}


def _score_note(coverage: float, missing: list[str], n: int) -> str:
    rel = f"Relative score among {n} eligible option{'s' if n != 1 else ''} (min-max normalised), not an absolute quality rating."
    if not missing:
        return f"{rel} All four factors available."
    names = ", ".join(FACTOR_NAMES[f] for f in missing)
    return (f"{rel} Based on {coverage * 100:.0f}% of the ranking model: {names} missing, "
            f"so their weight was redistributed. Treat with lower confidence than a fully evidenced score.")


def explain_pair(a: dict, b: dict) -> dict:
    """Why candidate `a` (ranked higher) is above candidate `b`, factor by factor."""
    rows = []
    ba = {r["factor"]: r for r in a.get("score_breakdown", [])}
    bb = {r["factor"]: r for r in b.get("score_breakdown", [])}
    for f in FACTORS:
        ra, rb = ba.get(f, {}), bb.get(f, {})
        ca, cb = ra.get("contribution"), rb.get("contribution")
        rows.append({"factor": f, "first_value": ra.get("raw_value"), "second_value": rb.get("raw_value"),
                     "first_contribution": ca, "second_contribution": cb,
                     "difference": round(ca - cb, 2) if ca is not None and cb is not None else None,
                     "note": None if ca is not None and cb is not None else
                     ("missing for #1" if ca is None and cb is not None else "missing for #2" if cb is None and ca is not None else "missing for both")})
    sentences = []
    da, db = a.get("policy", {}).get("decision"), b.get("policy", {}).get("decision")
    order = {"ALLOWED": 0, "REQUIRES_APPROVAL": 1, "BLOCKED": 2}
    if da and db and order.get(da, 9) < order.get(db, 9):
        sentences.append(f"#1 is ranked first because its policy outcome is {da} while #2 is {db}; policy outcome is "
                         "considered before score.")
    elif a.get("tier") == "FULLY_VERIFIED" and b.get("tier") != "FULLY_VERIFIED":
        sentences.append("#1 is fully verified (price, availability, allergens, quantity) while #2 still needs verification; "
                         "verification level is considered before score.")
    elif b.get("source") == "DATASET_SUBSTITUTION" and a.get("source") != "DATASET_SUBSTITUTION":
        sentences.append("#2 is a substitute product; originals are preferred at equal policy outcome and verification level.")
    sa, sb = a.get("score", 0) * 100, b.get("score", 0) * 100
    sentences.append(f"Score {sa:.1f} vs {sb:.1f} (difference {sa - sb:+.1f} points).")
    unit = {"price": " (total excl. VAT)", "reliability": " (historical)", "sustainability": " kg CO2e/kg (group average)", "match": ""}
    for r in sorted([r for r in rows if r["difference"] is not None], key=lambda r: -abs(r["difference"])):
        if abs(r["difference"]) < 0.05:
            continue
        who = "#1" if r["difference"] > 0 else "#2"
        fa, fb = _fmt(r["factor"], r["first_value"]), _fmt(r["factor"], r["second_value"])
        if fa == fb:
            sentences.append(f"{FACTOR_NAMES[r['factor']].capitalize()}: identical value ({fa}{unit[r['factor']]}); the "
                             f"{abs(r['difference']):.1f}-point difference comes only from weight redistribution, because "
                             f"the two products have different missing factors — not from better performance.")
        else:
            sentences.append(f"{FACTOR_NAMES[r['factor']].capitalize()}: {who} gains {abs(r['difference']):.1f} points "
                             f"({fa} vs {fb}{unit[r['factor']]}).")
    for r in rows:
        if r["note"]:
            sentences.append(f"{FACTOR_NAMES[r['factor']].capitalize()} {r['note']}: not scored, weight redistributed.")
    if a.get("evidence_coverage", 1) != b.get("evidence_coverage", 1):
        sentences.append(f"Evidence coverage differs: #1 uses {a.get('evidence_coverage', 0) * 100:.0f}% of the model, "
                         f"#2 uses {b.get('evidence_coverage', 0) * 100:.0f}% — scores are not fully like-for-like.")
    return {"first": a["id"], "second": b["id"], "factors": rows, "sentences": sentences}


def _fmt(f: str, v) -> str:
    if v is None:
        return "missing"
    if f == "price":
        return f"{v:.2f}"
    if f in ("reliability", "match"):
        return f"{v * 100:.1f}%"
    return f"{v:.3g}"


def explain(c: dict, peers: list[dict]) -> tuple[list[str], list[str]]:
    """Reasons and trade-offs in plain language."""
    reasons, tradeoffs = [], []
    m = c["metrics"]
    n_priced = sum(1 for p in peers if p["metrics"].get("price") is not None)
    if m.get("price") is not None:
        if c["factor_scores"].get("price") == 1.0 and n_priced > 1:
            reasons.append(f"Lowest verified total among {n_priced} priced options ({m['price']:.2f} {c.get('currency') or ''})".strip())
        else:
            reasons.append(f"Verified total {m['price']:.2f} {c.get('currency') or ''}".strip())
    if m.get("reliability") is not None:
        reasons.append(f"Historical delivery reliability {m['reliability'] * 100:.1f}%")
    if m.get("sustainability") is not None:
        reasons.append(f"Estimated {m['sustainability']:.3g} kg CO2e/kg (product-group average)")
    reasons.append(f"Request match {m['match'] * 100:.0f}%")
    reasons = [r.replace("Historical delivery reliability", "Historical delivery reliability (past performance, not a guarantee)") for r in reasons]
    for f in c["missing_factors"]:
        label = {"price": "price not verified", "reliability": "no reliability data", "sustainability": "no carbon data",
                 "match": "no match score"}[f]
        tradeoffs.append(f"{label} — weight redistributed to other factors")
    better = [p for p in peers if p is not c and p.get("score") is not None]
    for f in ("price", "reliability", "sustainability"):
        mine = m.get(f)
        if mine is None:
            continue
        rivals = [p["metrics"].get(f) for p in better if p["metrics"].get(f) is not None]
        if not rivals:
            continue
        best = min(rivals) if f in LOWER_IS_BETTER else max(rivals)
        if (f in LOWER_IS_BETTER and best < mine) or (f not in LOWER_IS_BETTER and best > mine):
            if f == "price":
                tradeoffs.append(f"Another option is {mine - best:.2f} cheaper")
            elif f == "reliability":
                tradeoffs.append(f"Another option has higher reliability ({best * 100:.1f}%)")
            else:
                tradeoffs.append(f"Another option has a lower carbon estimate ({best:.3g} kg CO2e/kg)")
    return reasons, tradeoffs
