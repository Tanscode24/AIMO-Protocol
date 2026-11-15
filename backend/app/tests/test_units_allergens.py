from types import SimpleNamespace

import json

import pytest

from app.models import Product
from app.services.allergens import evaluate_claim, evaluate_exclusion, evaluate_exclusions
from app.services.units import convert_request, is_valid_lot, parse_name_amount, round_to_lot


# ---------------------------------------------------------------- units
@pytest.mark.parametrize("name,count,amount,dim", [
    ("Valio semi skimmed milk 1 l lactose free UHT", 1, 1.0, "volume"),
    ("12 x Arla Whole milk drink 1 L UHT lactose free", 12, 1.0, "volume"),
    ("Barilla Spaghetti red lens pasta 250gx18", 18, 0.25, "weight"),
    ("Valio butter 4x5 kg unsalted", 4, 5.0, "weight"),
    ("Lurpak butter portion 100 pcs x 8 g", 100, 0.008, "weight"),
    ("Valio Eila semi skimmed milk drink 2,5 dl", 1, 0.25, "volume"),
])
def test_parse_name_amount(name, count, amount, dim):
    r = parse_name_amount(name)
    assert r["count"] == count and r["amount"] == pytest.approx(amount) and r["dimension"] == dim


def test_parse_ignores_percentages():
    assert parse_name_amount("Arla milk 1,5% fat") is None


def test_round_to_lot():
    assert round_to_lot(1000, 0.01, "ST") == 1000
    assert round_to_lot(66.6, 0.01, "KI") == 67  # whole cartons
    assert round_to_lot(7, 2, "KG") == 8  # multiple of lot size
    assert round_to_lot(3.33, 0.01, "KG") == pytest.approx(3.33)
    assert round_to_lot(5, 1.5, "KG") == 6


def test_lot_validation():
    assert is_valid_lot(4, 2) and not is_valid_lot(5, 2) and is_valid_lot(3.33, 0.01)


def test_conversion_verified_litres(db):
    p = db.get(Product, "464201")  # 1 l carton, salesUnit ST
    c = convert_request(p, 1000, "L")
    assert c["status"] == "VERIFIED" and c["quantity_sales_units"] == 1000


def test_conversion_case_level(db):
    p = db.get(Product, "469330")  # 5 x 1 L, salesUnit KI, CASE volume 5 L
    c = convert_request(p, 1000, "L")
    assert c["status"] == "VERIFIED" and c["quantity_sales_units"] == 200


def test_conversion_conflict_detected(db):
    p = db.get(Product, "464205")  # "Valio butter 4x5 kg": hierarchy implies 80 kg, name says 20 kg
    c = convert_request(p, 200, "KG")
    assert c["status"] == "CONFLICT" and c["quantity_sales_units"] is None


def test_conversion_unknown_is_not_guessed(db):
    p = db.get(Product, "464119")  # no unitConversions volume at sales level
    c = convert_request(p, 1000, "L")
    assert c["status"] in ("ESTIMATED", "UNKNOWN", "VERIFIED")
    if c["status"] == "UNKNOWN":
        assert c["quantity_sales_units"] is None


def test_sales_unit_quantity_respects_lot():
    p = SimpleNamespace(id="x", sales_unit="KG", base_unit="KG", allowed_lot_size=2.0, units_json="[]",
                        unit_conversions_json="[]", name_en="", name_fi="")
    c = convert_request(p, 5, "SALES_UNIT")
    assert c["quantity_sales_units"] == 6


# ---------------------------------------------------------------- allergens
def fake(allergens=None, free=None, claims=None, status=None, name="x"):
    allergens = allergens or []
    free = free or []
    return SimpleNamespace(
        allergens_json=json.dumps(allergens), non_allergens_json=json.dumps(free), claims_json=json.dumps(claims or []),
        allergen_data_status=status or ("DECLARED" if allergens or free else "UNKNOWN"),
        name_en=name, name_fi=None, name_sv=None)


def test_contains_may_contain_free_from_distinguished():
    p = fake([{"code": "AM", "relation": "CONTAINS"}, {"code": "AN", "relation": "MAY_CONTAIN"}], ["AE"])
    assert evaluate_exclusion(p, "AM")["status"] == "CONTAINS"
    assert evaluate_exclusion(p, "AN")["status"] == "MAY_CONTAIN"
    assert evaluate_exclusion(p, "AE")["status"] == "FREE_FROM"
    assert evaluate_exclusion(p, "AY")["status"] == "NOT_DECLARED"


def test_may_contain_is_a_violation():
    p = fake([{"code": "AN", "relation": "MAY_CONTAIN"}])
    assert evaluate_exclusions(p, ["AN"])["overall"] == "VIOLATION"


def test_missing_allergen_data_is_unknown_never_safe():
    p = fake()
    r = evaluate_exclusions(p, ["AM"])
    assert r["overall"] == "UNKNOWN"
    p2 = fake([{"code": "AW", "relation": "CONTAINS"}])  # declares something, but not milk
    assert evaluate_exclusions(p2, ["AM"])["overall"] == "UNKNOWN"


def test_claim_equivalence_counts_as_free_from():
    p = fake([], [], ["FREE_FROM_GLUTEN"], status="DECLARED")
    assert evaluate_exclusion(p, "AW")["status"] == "FREE_FROM"


def test_lactose_free_is_not_milk_free(db):
    p = db.get(Product, "464201")  # lactose-free milk declares FREE_FROM_LACTOSE and AM CONTAINS
    assert evaluate_claim(p, "FREE_FROM_LACTOSE")["status"] == "VERIFIED"
    assert evaluate_exclusion(p, "AM")["status"] == "CONTAINS"


def test_low_lactose_does_not_satisfy_lactose_free():
    p = fake([], [], ["LOW_LACTOSE"], status="DECLARED", name="Arla milk low lactose")
    assert evaluate_claim(p, "FREE_FROM_LACTOSE")["status"] == "NOT_MET"


def test_name_only_claim_is_unverified():
    p = fake([], [], [], status="UNKNOWN", name="Some milk lactose free 1 l")
    assert evaluate_claim(p, "FREE_FROM_LACTOSE")["status"] == "NAME_ONLY"
