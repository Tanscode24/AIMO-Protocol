"""Allergen and dietary-claim evaluation.

Source data: synkkaData.classifications groups `allergen` (CONTAINS / MAY_CONTAIN),
`nonAllergen` (FREE_FROM) and `nutritionalClaim` (e.g. FREE_FROM_LACTOSE).

The dataset contains only allergen *codes*. The labels below come from the public GS1
AllergenTypeCode list and are shown next to the raw code, never instead of it.

Safety rule: an allergen exclusion is only VERIFIED_SAFE when the product explicitly
declares FREE_FROM for that code (or an equivalent nutritional claim). A product that simply
does not mention the allergen is NOT_DECLARED, which is treated as UNKNOWN.
Lactose-free is a claim (FREE_FROM_LACTOSE) and is never treated as milk-free (AM).
"""
from __future__ import annotations

import json
from typing import Any

GS1_ALLERGEN_LABELS = {
    "AC": "Crustaceans", "AE": "Eggs", "AF": "Fish", "AM": "Milk", "AN": "Tree nuts", "AP": "Peanuts",
    "AS": "Sesame", "AU": "Sulphur dioxide / sulphites", "AW": "Cereals containing gluten", "AY": "Soybeans",
    "BC": "Celery", "BM": "Mustard", "GB": "Barley", "GK": "Kamut", "GO": "Oats", "GS": "Spelt",
    "ML": "Lactose", "NL": "Lupin", "NR": "Rye", "SA": "Almonds", "SC": "Cashews", "SH": "Hazelnuts",
    "SM": "Macadamia nuts", "SP": "Pecan nuts", "SR": "Brazil nuts", "ST": "Pistachios", "SW": "Walnuts",
    "UM": "Molluscs", "UW": "Wheat", "X99": "No declarable allergens (code X99)",
}
ALLERGEN_LABEL_SOURCE = "GS1 AllergenTypeCode list (external reference; dataset contains codes only)"

TREE_NUT_CODES = ["AN", "SA", "SC", "SH", "SM", "SP", "SR", "ST", "SW"]

# Request vocabulary -> allergen codes that must be excluded
ALLERGEN_WORDS: dict[str, list[str]] = {
    "milk": ["AM"], "dairy": ["AM"], "maito": ["AM"],
    "egg": ["AE"], "eggs": ["AE"],
    "gluten": ["AW"], "wheat": ["UW"], "rye": ["NR"], "barley": ["GB"], "oat": ["GO"], "oats": ["GO"],
    "soy": ["AY"], "soya": ["AY"],
    "peanut": ["AP"], "peanuts": ["AP"],
    "nut": TREE_NUT_CODES, "nuts": TREE_NUT_CODES, "tree nut": TREE_NUT_CODES, "tree nuts": TREE_NUT_CODES,
    "almond": ["SA"], "hazelnut": ["SH"], "walnut": ["SW"], "cashew": ["SC"],
    "fish": ["AF"], "shellfish": ["AC", "UM"], "crustacean": ["AC"], "crustaceans": ["AC"],
    "mollusc": ["UM"], "molluscs": ["UM"],
    "sesame": ["AS"], "mustard": ["BM"], "celery": ["BC"], "lupin": ["NL"],
    "sulphite": ["AU"], "sulphites": ["AU"], "sulfite": ["AU"], "sulfites": ["AU"],
}

# Nutritional claims that explicitly declare freedom from an allergen code
CLAIM_IMPLIES_FREE_FROM = {
    "FREE_FROM_GLUTEN": "AW",
    "FREE_FROM_EGGS": "AE",
    "FREE_FROM_SOY": "AY",
    "FREE_FROM_WHEAT": "UW",
}

# Dietary attributes (verified via nutritionalClaim)
ATTRIBUTE_WORDS: dict[str, str] = {
    "lactose-free": "FREE_FROM_LACTOSE", "lactose free": "FREE_FROM_LACTOSE", "laktoositon": "FREE_FROM_LACTOSE",
    "low-lactose": "LOW_LACTOSE", "low lactose": "LOW_LACTOSE",
    "vegan": "VEGAN", "vegetarian": "VEGETARIAN",
    "no added sugar": "NO_ADDED_SUGARS", "no added sugars": "NO_ADDED_SUGARS", "sugar-free": "NO_ADDED_SUGARS",
    "low-fat": "LOW_FAT", "low fat": "LOW_FAT", "fat-free": "FREE_FROM_FAT", "fat free": "FREE_FROM_FAT",
    "high-protein": "HIGH_PROTEIN", "high protein": "HIGH_PROTEIN", "high-fibre": "HIGH_FIBRE", "high fiber": "HIGH_FIBRE",
    "additive-free": "FREE_FROM_ADDITIVES", "preservative-free": "FREE_FROM_PRESERVATIVES",
}
# Name keywords accepted only as unverified ("NAME_ONLY") evidence
ATTRIBUTE_NAME_KEYWORDS: dict[str, list[str]] = {
    "FREE_FROM_LACTOSE": ["lactose free", "lactose-free", "lactosefree", "laktoositon", "laktosfri"],
    "LOW_LACTOSE": ["low lactose", "vähälaktoosinen"],
    "VEGAN": ["vegan", "vegaani"],
    "VEGETARIAN": ["vegetarian", "kasvis"],
    "FREE_FROM_GLUTEN": ["gluten free", "gluten-free", "glutenfree", "gluteeniton"],
    "NO_ADDED_SUGARS": ["no added sugar", "sugar free", "sokeroimaton"],
    "LOW_FAT": ["low fat", "rasvaton", "fat free"],
    "FREE_FROM_FAT": ["fat free", "rasvaton"],
    "HIGH_PROTEIN": ["protein"],
    "ORGANIC": ["organic", "luomu", "ekologisk"],
}
KNOWN_CLAIMS = set(ATTRIBUTE_WORDS.values()) | {"ORGANIC", "FREE_FROM_GLUTEN"}


def label(code: str) -> str:
    return GS1_ALLERGEN_LABELS.get(code, code)


def _product_sets(product: Any) -> tuple[dict[str, set[str]], set[str], set[str]]:
    allergens = json.loads(product.allergens_json or "[]")
    contains: dict[str, set[str]] = {}
    for a in allergens:
        contains.setdefault(a["code"], set()).add(a["relation"])
    free = set(json.loads(product.non_allergens_json or "[]"))
    claims = set(json.loads(product.claims_json or "[]"))
    return contains, free, claims


def evaluate_exclusion(product: Any, code: str) -> dict:
    """Return status for one allergen code that must NOT be present."""
    contains, free, claims = _product_sets(product)
    relations = contains.get(code, set())
    base = {"code": code, "label": label(code), "label_source": ALLERGEN_LABEL_SOURCE}
    if "CONTAINS" in relations:
        return {**base, "status": "CONTAINS", "verified": True, "detail": f"Declares {code} CONTAINS"}
    if "MAY_CONTAIN" in relations:
        return {**base, "status": "MAY_CONTAIN", "verified": True, "detail": f"Declares {code} MAY_CONTAIN (traces)"}
    if code in free:
        return {**base, "status": "FREE_FROM", "verified": True, "detail": f"Declares {code} FREE_FROM (nonAllergen)"}
    for claim, c in CLAIM_IMPLIES_FREE_FROM.items():
        if c == code and claim in claims:
            return {**base, "status": "FREE_FROM", "verified": True, "detail": f"Nutritional claim {claim}"}
    if product.allergen_data_status == "DECLARED":
        return {**base, "status": "NOT_DECLARED", "verified": False,
                "detail": f"Allergen list exists but {code} is neither declared present nor FREE_FROM"}
    return {**base, "status": "UNKNOWN", "verified": False, "detail": "No allergen information in product data"}


def evaluate_exclusions(product: Any, codes: list[str]) -> dict:
    """Aggregate: VIOLATION (contains / may contain) | UNKNOWN | VERIFIED_SAFE | NOT_REQUIRED."""
    if not codes:
        return {"overall": "NOT_REQUIRED", "checks": []}
    checks = [evaluate_exclusion(product, c) for c in codes]
    if any(c["status"] in ("CONTAINS", "MAY_CONTAIN") for c in checks):
        overall = "VIOLATION"
    elif all(c["status"] == "FREE_FROM" for c in checks):
        overall = "VERIFIED_SAFE"
    else:
        overall = "UNKNOWN"
    return {"overall": overall, "checks": checks}


def evaluate_claim(product: Any, claim: str) -> dict:
    """VERIFIED (declared claim) | NAME_ONLY (only product name suggests it) | NOT_MET."""
    contains, free, claims = _product_sets(product)
    if claim in claims:
        return {"claim": claim, "status": "VERIFIED", "detail": f"nutritionalClaim {claim}"}
    if claim == "FREE_FROM_LACTOSE" and "ML" in free:
        return {"claim": claim, "status": "VERIFIED", "detail": "nonAllergen ML (lactose) FREE_FROM"}
    if claim == "FREE_FROM_GLUTEN" and "AW" in free:
        return {"claim": claim, "status": "VERIFIED", "detail": "nonAllergen AW FREE_FROM"}
    names = " ".join(filter(None, [product.name_en, product.name_fi, product.name_sv])).lower()
    for kw in ATTRIBUTE_NAME_KEYWORDS.get(claim, []):
        if kw in names:
            # A low-lactose product never satisfies a lactose-free requirement
            if claim == "FREE_FROM_LACTOSE" and "LOW_LACTOSE" in claims:
                break
            return {"claim": claim, "status": "NAME_ONLY", "detail": f"Product name mentions '{kw}', no declared claim"}
    note = ""
    if claim == "FREE_FROM_LACTOSE" and "LOW_LACTOSE" in claims:
        note = " (product is LOW_LACTOSE, which is not lactose-free)"
    return {"claim": claim, "status": "NOT_MET", "detail": f"No declared {claim}{note}"}


def product_allergen_summary(product: Any) -> dict:
    contains, free, claims = _product_sets(product)
    return {
        "status": product.allergen_data_status,
        "contains": sorted(c for c, r in contains.items() if "CONTAINS" in r),
        "may_contain": sorted(c for c, r in contains.items() if "MAY_CONTAIN" in r and "CONTAINS" not in r),
        "free_from": sorted(free),
        "claims": sorted(claims),
        "labels": {c: label(c) for c in set(contains) | free},
        "label_source": ALLERGEN_LABEL_SOURCE,
    }
