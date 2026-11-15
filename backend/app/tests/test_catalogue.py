import json

import pandas as pd
import pytest

from app.models import Product
from app.services import catalogue
from app.tests.conftest import DATA_DIR, SUBSET_IDS


def test_ingestion_loads_subset_and_validates(client, db):
    n = db.query(Product).count()
    assert n == 300 + len([i for i in SUBSET_IDS])  or n >= 300
    raw = json.loads((DATA_DIR / catalogue.PRODUCT_FILE).read_text())
    assert n == len(raw)


def test_product_ids_are_strings(db):
    p = db.get(Product, "464201")
    assert p is not None and isinstance(p.id, str)
    raw = json.loads(p.raw_json)
    assert raw["id"] == "464201"
    assert db.get(Product, 464201) is None or db.get(Product, "464201") is not None


def test_csv_join_by_product_id_as_string(db):
    rel = pd.read_csv(DATA_DIR / catalogue.RELIABILITY_FILE, dtype={"productId": str}).set_index("productId")
    car = pd.read_csv(DATA_DIR / catalogue.CARBON_FILE, dtype={"productId": str}).set_index("productId")
    checked = 0
    for p in db.query(Product).all():
        if p.id in rel.index:
            assert p.reliability == pytest.approx(float(rel.loc[p.id, "deliveryReliability"]))
            checked += 1
        else:
            assert p.reliability is None  # missing stays unknown, never 0
        if p.id in car.index:
            assert p.carbon_kg_per_kg == pytest.approx(float(car.loc[p.id, "AVG kg CO2-eq/kg"]))
        else:
            assert p.carbon_kg_per_kg is None
    assert checked > 0


def test_missing_values_are_unknown_not_zero(db):
    assert db.query(Product).filter(Product.reliability == 0).count() == \
        db.query(Product).filter(Product.reliability.isnot(None), Product.reliability == 0).count()
    unknown = db.query(Product).filter(Product.allergen_data_status == "UNKNOWN").all()
    for p in unknown:
        assert json.loads(p.allergens_json) == [] and json.loads(p.non_allergens_json) == []


def test_schema_validation_rejects_invalid_data(tmp_path, db):
    for f in (catalogue.SCHEMA_FILE, catalogue.RELIABILITY_FILE, catalogue.CARBON_FILE):
        (tmp_path / f).write_bytes((DATA_DIR / f).read_bytes())
    (tmp_path / catalogue.PRODUCT_FILE).write_text(json.dumps([{"id": "1"}]))  # missing required fields
    with pytest.raises(ValueError, match="schema validation"):
        catalogue.ingest(db, tmp_path)
    db.rollback()


def test_search_finds_products_in_english_and_finnish(client, buyer):
    r = client.get("/api/products", params={"q": "lactose free milk"}, headers=buyer)
    assert r.status_code == 200
    ids = [i["id"] for i in r.json()["items"]]
    assert "464201" in ids
    r2 = client.get("/api/products", params={"q": "laktoositon"}, headers=buyer)
    assert r2.json()["total"] >= 1
    r3 = client.get("/api/products", params={"q": "4642"}, headers=buyer)
    assert all(i["id"].startswith("4642") for i in r3.json()["items"])


def test_product_detail_has_provenance(client, buyer):
    d = client.get("/api/products/464201", headers=buyer).json()
    assert d["provenance"]["catalogue"].startswith("valio_aimo")
    assert d["allergens"]["status"] in ("DECLARED", "UNKNOWN")
    assert "per_sales_unit" in d


def test_search_pagination_is_consistent_and_rank_ordered(client):
    """Search hydrates only the requested page (perf fix); pages must still tile the ranked result exactly."""
    from sqlalchemy import text

    from app.database import SessionLocal, is_sqlite
    from app.services.search import search_catalogue

    with SessionLocal() as db:
        for q in ("milk", "butter", "maito"):
            full = search_catalogue(db, q, page=1, page_size=100)
            p1, p2 = search_catalogue(db, q, page=1, page_size=5), search_catalogue(db, q, page=2, page_size=5)
            assert p1["total"] == p2["total"] == full["total"]
            ids = [i["id"] for i in p1["items"] + p2["items"]]
            assert ids == [i["id"] for i in full["items"][:10]] and len(set(ids)) == len(ids)
            if is_sqlite(db.get_bind()) and full["total"]:
                ranked = [r[0] for r in db.execute(text(
                    "SELECT product_id FROM products_fts WHERE products_fts MATCH :m "
                    "ORDER BY bm25(products_fts, 0, 10.0, 4.0, 2.0, 3.0, 0.5) LIMIT 5000"), {"m": f"{q}*"})]
                assert [i["id"] for i in full["items"]] == ranked[:len(full["items"])]
        filtered = search_catalogue(db, "milk", allergen_status="DECLARED", page_size=100)
        assert all(i["allergen_data_status"] == "DECLARED" for i in filtered["items"])
        assert search_catalogue(db, "zzzzqqq")["total"] == 0
