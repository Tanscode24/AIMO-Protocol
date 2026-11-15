"""Catalogue ingestion: validate the official product JSON against the official schema,
normalise it, join reliability + carbon CSVs on product id (as strings) and build the
FTS5 search index.

Run manually:  python -m scripts.ingest      (from backend/)
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import jsonschema
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import is_sqlite
from app.models import Product

log = logging.getLogger("app.catalogue")

PRODUCT_FILE = "valio_aimo_junction26_product_data.json"
SCHEMA_FILE = "valio_aimo_junction26_product_data_schema.json"
RELIABILITY_FILE = "valio_aimo_junction26_delivery_reliability.csv"
CARBON_FILE = "valio_aimo_junction26_carbon_footprint.csv"
CARBON_SOURCE_COLUMN = "AVG kg CO2-eq/kg"

# Fields that make a product record useful for autonomous procurement
COMPLETENESS_FIELDS = [
    "name_en", "brand", "description", "key_ingredients", "allergens", "unit_conversions",
    "nutrition", "reliability", "carbon", "gtin",
]


def _text(items: list[dict] | None, lang: str) -> str | None:
    for it in items or []:
        if it.get("language") == lang and it.get("value"):
            return it["value"].strip()
    return None


def _first_text(items: list[dict] | None, langs=("en", "fi", "sv")) -> str | None:
    for lang in langs:
        v = _text(items, lang)
        if v:
            return v
    for it in items or []:
        if it.get("value"):
            return it["value"].strip()
    return None


def _classification(sd: dict, name: str) -> list[dict]:
    for c in sd.get("classifications") or []:
        if c.get("name") == name:
            return c.get("values") or []
    return []


def load_csvs(data_dir: Path) -> tuple[dict[str, float], dict[str, float], dict]:
    rel = pd.read_csv(data_dir / RELIABILITY_FILE, dtype={"productId": str})
    car = pd.read_csv(data_dir / CARBON_FILE, dtype={"productId": str})
    stats = {}
    for name, df, col in (("reliability", rel, "deliveryReliability"), ("carbon", car, CARBON_SOURCE_COLUMN)):
        if "productId" not in df.columns or col not in df.columns:
            raise ValueError(f"{name} CSV missing expected columns productId/{col}")
        dups = int(df["productId"].duplicated().sum())
        if dups:
            raise ValueError(f"{name} CSV has {dups} duplicate productIds")
        df = df.dropna(subset=[col])
        stats[name] = {"rows": int(len(df)), "duplicates": dups}
    rel_map = {str(r.productId).strip(): float(r.deliveryReliability) for r in rel.dropna().itertuples()}
    car_map = {str(pid).strip(): float(v) for pid, v in zip(car["productId"], car[CARBON_SOURCE_COLUMN]) if pd.notna(v)}
    return rel_map, car_map, stats


def normalise(p: dict, rel: dict[str, float], car: dict[str, float]) -> dict:
    sd = p.get("synkkaData") or {}
    names = sd.get("names") or []
    allergens = [
        {"code": v["id"], "relation": (v.get("unit") or "CONTAINS").upper()}
        for v in _classification(sd, "allergen")
        if v.get("id")
    ]
    non_allergens = sorted({v["id"] for v in _classification(sd, "nonAllergen") if v.get("id") and (v.get("unit") or "FREE_FROM").upper() == "FREE_FROM"})
    claims = sorted({v["id"] for v in _classification(sd, "nutritionalClaim") if v.get("id")})
    pid = str(p["id"])
    name_en, name_fi, name_sv = _text(names, "en"), _text(names, "fi"), _text(names, "sv")
    key_ing = _first_text(sd.get("keyIngredients"))
    description = _first_text(sd.get("marketingTexts"))
    present = {
        "name_en": bool(name_en),
        "brand": bool(sd.get("brand")),
        "description": bool(description),
        "key_ingredients": bool(key_ing),
        "allergens": bool(allergens or non_allergens),
        "unit_conversions": bool(sd.get("unitConversions")),
        "nutrition": bool(sd.get("nutritionalContent")),
        "reliability": pid in rel,
        "carbon": pid in car,
        "gtin": bool(sd.get("gtin") or p.get("salesUnitGtin")),
    }
    missing = [k for k, v in present.items() if not v]
    vat = (sd.get("vatCode") or "").strip() or None
    return {
        "id": pid,
        "name_en": name_en,
        "name_fi": name_fi,
        "name_sv": name_sv,
        "display_name": name_en or name_fi or name_sv or _first_text(names) or f"Product {pid}",
        "brand": sd.get("brand"),
        "vendor_name": p.get("vendorName") or "",
        "category": str(p.get("category") or ""),
        "sales_unit": p.get("salesUnit") or "",
        "base_unit": p.get("baseUnit") or "",
        "allowed_lot_size": float(p.get("allowedLotSize") or 0),
        "temperature_condition": p.get("temperatureCondition"),
        "country_of_origin": p.get("countryOfOrigin"),
        "gtin": sd.get("gtin") or p.get("salesUnitGtin"),
        "description": description,
        "key_ingredients": key_ing,
        "units_json": json.dumps(p.get("units") or []),
        "unit_conversions_json": json.dumps(sd.get("unitConversions") or []),
        "allergens_json": json.dumps(allergens),
        "non_allergens_json": json.dumps(non_allergens),
        "claims_json": json.dumps(claims),
        "allergen_data_status": "DECLARED" if (allergens or non_allergens) else "UNKNOWN",
        "substitutions_json": json.dumps(
            [str(s["substituteProduct"]) for s in p.get("substitutions") or [] if str(s.get("product")) == pid]
        ),
        "deposits_json": json.dumps(p.get("deposits") or []),
        "vat_code": vat,
        "synkka_complete": "gtin" in sd,
        "synkka_last_updated": sd.get("lastUpdated"),
        "completeness": round(sum(present.values()) / len(present), 3),
        "missing_fields_json": json.dumps(missing),
        "reliability": rel.get(pid),
        "carbon_kg_per_kg": car.get(pid),
        "raw_json": json.dumps(p, ensure_ascii=False),
    }


def create_fts(db: Session) -> None:
    if not is_sqlite(db.get_bind()):
        return
    db.execute(text("DROP TABLE IF EXISTS products_fts"))
    db.execute(
        text(
            "CREATE VIRTUAL TABLE products_fts USING fts5("
            "product_id UNINDEXED, name_en, name_fi, name_sv, brand, extra, "
            "tokenize='unicode61 remove_diacritics 2')"
        )
    )
    db.execute(
        text(
            "INSERT INTO products_fts(product_id, name_en, name_fi, name_sv, brand, extra) "
            "SELECT id, coalesce(name_en,''), coalesce(name_fi,''), coalesce(name_sv,''), coalesce(brand,''), "
            "coalesce(vendor_name,'') || ' ' || coalesce(key_ingredients,'') FROM products"
        )
    )


def ingest(db: Session, data_dir: Path | None = None, validate: bool = True) -> dict:
    data_dir = Path(data_dir or get_settings().data_dir)
    t0 = time.time()
    products_path = data_dir / PRODUCT_FILE
    if not products_path.exists():
        raise FileNotFoundError(f"Product data not found at {products_path}")
    with products_path.open(encoding="utf-8") as f:
        raw = json.load(f)
    schema_errors = 0
    if validate:
        schema = json.loads((data_dir / SCHEMA_FILE).read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema)
        errors = list(validator.iter_errors(raw))
        schema_errors = len(errors)
        if errors:
            sample = "; ".join(e.message[:120] for e in errors[:5])
            raise ValueError(f"Product data failed schema validation ({len(errors)} errors): {sample}")
    rel, car, csv_stats = load_csvs(data_dir)
    ids = [str(p["id"]) for p in raw]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate product ids in product data")
    idset = set(ids)
    rows = [normalise(p, rel, car) for p in raw]
    db.query(Product).delete()
    db.bulk_insert_mappings(Product, rows)
    create_fts(db)
    db.commit()
    stats = {
        "products": len(rows),
        "schema_valid": schema_errors == 0,
        "schema_errors": schema_errors,
        "reliability_rows": csv_stats["reliability"]["rows"],
        "reliability_joined": sum(1 for i in ids if i in rel),
        "reliability_orphans": sum(1 for i in rel if i not in idset),
        "carbon_rows": csv_stats["carbon"]["rows"],
        "carbon_joined": sum(1 for i in ids if i in car),
        "carbon_orphans": sum(1 for i in car if i not in idset),
        "allergen_declared": sum(1 for r in rows if r["allergen_data_status"] == "DECLARED"),
        "seconds": round(time.time() - t0, 1),
    }
    log.info("Catalogue ingested: %s", stats)
    return stats


def catalogue_count(db: Session) -> int:
    return db.query(Product).count()
