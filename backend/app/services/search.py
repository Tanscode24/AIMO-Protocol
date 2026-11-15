"""Catalogue search (SQLite FTS5 with a LIKE fallback) and request-match scoring."""
from __future__ import annotations

import json
import re

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.database import is_sqlite
from app.models import Product
from app.services.allergens import product_allergen_summary

TOKEN_RE = re.compile(r"[0-9a-zA-ZäöåÄÖÅéèüÜ]+")
STOPWORDS = {"the", "a", "an", "of", "and", "or", "for", "with", "in", "to", "some", "any", "best", "good", "fresh", "quality"}

# Words that indicate a different product type when they are not part of the request
# ("milk chocolate" is not "milk", "butter bun" is not "butter").
DRIFT_WORDS = {
    "chocolate", "cocoa", "coffee", "latte", "cappuccino", "macchiato", "espresso", "mocca", "powder", "quark", "viili",
    "curdled", "fermented", "sour", "pudding", "cake", "bread", "bun", "buns", "croissant", "brioche", "cheese",
    "condensed", "cream", "yoghurt", "yogurt", "ice", "sauce", "spread", "filling", "flavour", "flavoured", "flavored",
    "dough", "mix", "flourmix", "soup", "snack", "bar", "biscuit", "biscuits", "cookie", "cookies", "candy", "praline",
    "bean", "beans", "squash", "chicken", "kiev", "patty", "potato", "pastry", "casserole", "ball", "balls", "sweet",
    "tea", "smoothie", "dessert", "skyr", "blend", "oil", "milkshake", "shake", "pastryplate", "plate",
    "base", "mayonnaise", "dressing", "salad", "pizza", "lasagne", "sausage", "ham", "nuggets", "butterbun",
    "baguette", "tahin", "tahini", "sesame", "peanut", "almond", "cashew", "hazelnut", "coconut", "oat", "soy", "rice",
    "paste", "pesto", "marinade", "seasoning", "spice", "crisps", "chips", "muffin", "donut", "pie", "tart", "waffle",
}


def tokens(s: str | None) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(s or "") if t.lower() not in STOPWORDS and len(t) > 1]


def _stem(t: str) -> str:
    for suf in ("ies", "es", "s"):
        if len(t) > 4 and t.endswith(suf):
            return t[: -len(suf)] + ("y" if suf == "ies" else "")
    return t


def _fts_escape(t: str) -> str:
    return '"' + t.replace('"', "") + '"'


def match_score(product: Product, query: str) -> dict:
    """How well a product name matches the requested product type (0..1).

    English names are authoritative when present (Finnish 'pasta' means 'paste', for example).
    Penalties: words signalling a different product type ("milk chocolate", "pastasauce"),
    and the requested noun appearing only as an ingredient ("baguette with garlic butter")."""
    q = [_stem(t) for t in tokens(query)]
    if not q:
        return {"score": 0.0, "matched": [], "drift": []}
    primary = product.name_en or product.name_fi or product.name_sv or ""
    raw_tokens = [t for t in tokens(primary)]
    name_tokens = [_stem(t) for t in raw_tokens]
    matched, positions = [], []
    compound_drift = set()
    for t in q:
        for i, n in enumerate(name_tokens):
            if n == t or (len(t) >= 4 and n.startswith(t)) or (len(n) >= 4 and t.startswith(n)):
                matched.append(t)
                positions.append(i)
                if n != t and n.startswith(t) and any(n.endswith(d) for d in DRIFT_WORDS if len(d) >= 4):
                    compound_drift.add(n)
                break
    coverage = len(matched) / len(q)
    drift = sorted(({n for n in name_tokens if n in DRIFT_WORDS and n not in q}) | compound_drift)
    ingredient_only = False
    low = primary.lower()
    for kw in (" with ", " made with ", " in ", " filled with ", " & "):
        idx = low.find(kw)
        if idx > 0 and matched:
            before = [_stem(t) for t in tokens(low[:idx])]
            if not any(m in before or any(b.startswith(m) for b in before) for m in matched):
                ingredient_only = True
            break
    score = coverage - 0.3 * len(drift) - (0.4 if ingredient_only else 0.0)
    score = max(0.0, min(1.0, score))
    return {"score": round(score, 3), "matched": matched, "drift": drift, "coverage": round(coverage, 3),
            "ingredient_only": ingredient_only}


def fts_candidate_ids(db: Session, query: str, limit: int = 1500) -> list[str]:
    toks = tokens(query)
    if not toks:
        return []
    if is_sqlite(db.get_bind()):
        expr = " OR ".join(f"{_fts_escape(t)}*" for t in toks)
        sql = text(
            "SELECT product_id FROM products_fts WHERE products_fts MATCH :m ORDER BY bm25(products_fts, 0, 10.0, 4.0, 2.0, 1.0, 0.2) LIMIT :lim"
        )
        try:
            return [r[0] for r in db.execute(sql, {"m": "{name_en name_fi name_sv brand} : (" + expr + ")", "lim": limit})]
        except Exception:
            pass
    conds = [Product.name_en.ilike(f"%{t}%") for t in toks] + [Product.name_fi.ilike(f"%{t}%") for t in toks]
    return [p.id for p in db.query(Product.id).filter(or_(*conds)).limit(limit)]


def search_catalogue(
    db: Session,
    q: str | None = None,
    *,
    brand: str | None = None,
    category: str | None = None,
    vendor: str | None = None,
    allergen_status: str | None = None,
    claim: str | None = None,
    free_from: str | None = None,
    has_reliability: bool | None = None,
    has_carbon: bool | None = None,
    sales_unit: str | None = None,
    page: int = 1,
    page_size: int = 24,
) -> dict:
    query = db.query(Product)
    q = (q or "").strip()
    order_ids: list[str] | None = None
    if q:
        if q.isdigit():
            query = query.filter(Product.id.like(f"{q}%"))
        else:
            toks = tokens(q)
            if toks and is_sqlite(db.get_bind()):
                expr = " AND ".join(f"{_fts_escape(t)}*" for t in toks)
                try:
                    order_ids = [r[0] for r in db.execute(
                        text("SELECT product_id FROM products_fts WHERE products_fts MATCH :m ORDER BY bm25(products_fts, 0, 10.0, 4.0, 2.0, 3.0, 0.5) LIMIT 5000"),
                        {"m": expr})]
                except Exception:
                    order_ids = None
            if order_ids is None:
                for t in toks:
                    query = query.filter(or_(Product.name_en.ilike(f"%{t}%"), Product.name_fi.ilike(f"%{t}%"),
                                             Product.brand.ilike(f"%{t}%")))
            else:
                query = query.filter(Product.id.in_(order_ids)) if order_ids else query.filter(text("1=0"))
    if brand:
        query = query.filter(Product.brand == brand)
    if category:
        query = query.filter(Product.category == category)
    if vendor:
        query = query.filter(Product.vendor_name == vendor)
    if allergen_status:
        query = query.filter(Product.allergen_data_status == allergen_status)
    if claim:
        query = query.filter(Product.claims_json.like(f'%"{claim}"%'))
    if free_from:
        query = query.filter(Product.non_allergens_json.like(f'%"{free_from}"%'))
    if has_reliability is not None:
        query = query.filter(Product.reliability.isnot(None) if has_reliability else Product.reliability.is_(None))
    if has_carbon is not None:
        query = query.filter(Product.carbon_kg_per_kg.isnot(None) if has_carbon else Product.carbon_kg_per_kg.is_(None))
    if sales_unit:
        query = query.filter(Product.sales_unit == sales_unit)
    page_size = max(1, min(page_size, 100))
    page = max(1, page)
    if order_ids:
        # Rank on ids only, then hydrate just the requested page. Loading every matching Product (up to 5,000
        # rows incl. raw_json) to return 24 made concurrent searches CPU/GIL-bound (p95 > 2 s at 8 clients).
        rank = {pid: i for i, pid in enumerate(order_ids)}
        matching = sorted((r[0] for r in query.with_entities(Product.id)), key=lambda pid: rank.get(pid, 10**9))
        total = len(matching)
        page_ids = matching[(page - 1) * page_size: page * page_size]
        rows = db.query(Product).filter(Product.id.in_(page_ids)).all() if page_ids else []
        items = sorted(rows, key=lambda p: rank.get(p.id, 10**9))
    else:
        total = query.count()
        items = query.order_by(Product.completeness.desc(), Product.id).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size, "items": [product_summary(p) for p in items]}


def product_summary(p: Product) -> dict:
    return {
        "id": p.id,
        "name": p.display_name,
        "name_en": p.name_en,
        "name_fi": p.name_fi,
        "name_sv": p.name_sv,
        "brand": p.brand,
        "vendor_name": p.vendor_name,
        "category": p.category,
        "sales_unit": p.sales_unit,
        "base_unit": p.base_unit,
        "allowed_lot_size": p.allowed_lot_size,
        "allergen_data_status": p.allergen_data_status,
        "claims": json.loads(p.claims_json or "[]"),
        "reliability": p.reliability,
        "carbon_kg_per_kg": p.carbon_kg_per_kg,
        "completeness": p.completeness,
        "missing_fields": json.loads(p.missing_fields_json or "[]"),
        "country_of_origin": p.country_of_origin,
        "has_substitutions": p.substitutions_json not in (None, "", "[]"),
    }


def product_detail(p: Product) -> dict:
    from app.services.units import per_sales_unit

    raw = json.loads(p.raw_json)
    sd = raw.get("synkkaData") or {}
    return {
        **product_summary(p),
        "gtin": p.gtin,
        "description": p.description,
        "key_ingredients": p.key_ingredients,
        "temperature_condition": p.temperature_condition,
        "vat_code": p.vat_code,
        "units": json.loads(p.units_json or "[]"),
        "unit_conversions": json.loads(p.unit_conversions_json or "[]"),
        "per_sales_unit": {"volume": per_sales_unit(p, "volume"), "weight": per_sales_unit(p, "weight")},
        "allergens": product_allergen_summary(p),
        "substitutions": json.loads(p.substitutions_json or "[]"),
        "deposits": json.loads(p.deposits_json or "[]"),
        "nutrition": sd.get("nutritionalContent") or [],
        "storage_instructions": next((t["value"] for t in sd.get("storageInstructions") or [] if t.get("language") == "en"), None),
        "producers": sd.get("producers") or [],
        "synkka_complete": p.synkka_complete,
        "synkka_last_updated": p.synkka_last_updated,
        "provenance": {
            "catalogue": "valio_aimo_junction26_product_data.json (schema-validated)",
            "reliability": "valio_aimo_junction26_delivery_reliability.csv (historical, product-specific)" if p.reliability is not None else None,
            "carbon": "valio_aimo_junction26_carbon_footprint.csv (product-group average, kg CO2e per kg)" if p.carbon_kg_per_kg is not None else None,
        },
    }
