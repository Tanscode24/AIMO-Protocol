from datetime import date
from types import SimpleNamespace

import pytest

from app.services.parser import parse_date_phrase, rule_parse
from app.services.policy import ALLOWED, BLOCKED, REQUIRES_APPROVAL, DecisionContext, evaluate
from app.services.ranking import rank, weights_from_policy

TODAY = date(2026, 11, 14)  # Saturday


# ---------------------------------------------------------------- parser
def test_parse_demo_scenario():
    i = rule_parse("I need 1,000 litres of lactose-free milk by Monday. Find the best option based on price and availability.", TODAY)
    assert i.quantity == 1000 and i.unit == "L" and i.product_query == "milk"
    assert i.delivery_date == date(2026, 11, 16)
    assert i.required_attributes == ["FREE_FROM_LACTOSE"]
    assert "AM" not in i.excluded_allergens  # lactose-free is not milk-free
    assert "price" in i.priorities


def test_parse_allergen_kitchen():
    i = rule_parse("we need 20 kg of gluten-free pasta, it must be milk-free and egg-free, delivered by Friday", TODAY)
    assert i.quantity == 20 and i.unit == "KG" and i.product_query == "pasta"
    assert set(i.excluded_allergens) == {"AW", "AM", "AE"}
    assert i.delivery_date == date(2026, 11, 20)


def test_parse_budget_and_cases():
    i = rule_parse("Order 50 cases of tomato ketchup on 2026-11-20 with a budget of €400, no mustard", TODAY)
    assert i.unit == "SALES_UNIT" and i.quantity == 50 and i.budget_limit == 400 and i.excluded_allergens == ["BM"]


def test_parse_quantity_after_product():
    i = rule_parse("Find lactose-free milk for 1000 litres delivered by Monday.", TODAY)
    assert i.product_query == "milk" and i.quantity == 1000


@pytest.mark.parametrize("text,expected", [
    ("tomorrow", date(2026, 11, 15)), ("by Monday", date(2026, 11, 16)), ("on 20.11.2026", date(2026, 11, 20)),
    ("in 3 days", date(2026, 11, 17)), ("2026-12-01", date(2026, 12, 1)), ("25 November", date(2026, 11, 25)),
])
def test_dates(text, expected):
    assert parse_date_phrase(text, TODAY)[0] == expected


def test_missing_date_is_flagged_assumption():
    i = rule_parse("we need 10 kg of butter", TODAY)
    assert i.delivery_date == date(2026, 11, 16) and any("assumed" in a for a in i.assumptions)


# ---------------------------------------------------------------- ranking
POL = SimpleNamespace(weight_price=0.4, weight_reliability=0.3, weight_sustainability=0.2, weight_match=0.1)


def cand(i, price=None, rel=None, carbon=None, match=1.0):
    return {"id": i, "metrics": {"price": price, "reliability": rel, "sustainability": carbon, "match": match}}


def test_min_max_and_direction():
    w = weights_from_policy(POL)
    r = {c["id"]: c for c in rank([cand("a", 100, 0.9, 1.0), cand("b", 200, 1.0, 2.0)], w)}
    assert r["a"]["factor_scores"]["price"] == 1.0 and r["b"]["factor_scores"]["price"] == 0.0
    assert r["b"]["factor_scores"]["reliability"] == 1.0
    assert r["a"]["factor_scores"]["sustainability"] == 1.0


def test_missing_factor_is_removed_and_renormalised_not_zero():
    w = weights_from_policy(POL)
    r = {c["id"]: c for c in rank([cand("a", 100, None, 1.0), cand("b", 200, 1.0, 2.0)], w)}
    a = r["a"]
    assert "reliability" in a["missing_factors"] and "reliability" not in a["factor_scores"]
    assert sum(a["effective_weights"].values()) == pytest.approx(1.0, abs=1e-3)
    assert a["score"] > 0.5  # best price & carbon; missing reliability did not count as zero


def test_priorities_boost_weights():
    w = weights_from_policy(POL, ["reliability"])
    assert w["reliability"] > 0.3


# ---------------------------------------------------------------- policy
POLICY = {
    "id": "p", "version": 1, "max_order_value": 500, "max_quantity": None, "auto_approval_enabled": True,
    "require_human_approval_above_limit": True, "allowed_categories": [], "blocked_categories": ["999"],
    "blocked_products": ["BAD"], "allow_substitutions": True, "substitution_requires_approval": True,
    "unknown_allergen_action": "BLOCK", "unverified_price_action": "REQUIRE_APPROVAL",
    "unverified_availability_action": "REQUIRE_APPROVAL", "terminal_quantity_action": "REQUIRE_APPROVAL",
    "unverified_conversion_action": "REQUIRE_APPROVAL", "allow_late_delivery": False, "duplicate_window_minutes": 60,
}


def ctx(**kw):
    base = dict(product_id="1", category="100", quantity_sales_units=10, conversion_status="VERIFIED",
                price_status="VERIFIED", total_excl_tax=100.0, availability_status="AVAILABLE")
    base.update(kw)
    return DecisionContext(**base)


@pytest.mark.parametrize("kw,policy_over,expected,code", [
    ({}, {}, ALLOWED, "WITHIN_POLICY"),
    ({"total_excl_tax": 900.0}, {}, REQUIRES_APPROVAL, "SPEND_LIMIT"),
    ({"total_excl_tax": 900.0}, {"require_human_approval_above_limit": False}, BLOCKED, "SPEND_LIMIT"),
    ({"category": "999"}, {}, BLOCKED, "CATEGORY_BLOCKED"),
    ({"product_id": "BAD"}, {}, BLOCKED, "PRODUCT_BLOCKED"),
    ({"availability_status": "UNAVAILABLE"}, {}, BLOCKED, "UNAVAILABLE"),
    ({"availability_status": "PARTIAL"}, {}, BLOCKED, "INSUFFICIENT_STOCK"),
    ({"availability_status": "LATE"}, {}, BLOCKED, "LATE_DELIVERY"),
    ({"availability_status": "LATE"}, {"allow_late_delivery": True}, REQUIRES_APPROVAL, "LATE_DELIVERY"),
    ({"availability_status": "AVAILABLE_QTY_UNCONFIRMED"}, {}, REQUIRES_APPROVAL, "QUANTITY_UNCONFIRMED"),
    ({"availability_status": "AVAILABLE_QTY_UNCONFIRMED"}, {"terminal_quantity_action": "ALLOW"}, ALLOWED, "QUANTITY_UNCONFIRMED"),
    ({"availability_status": "TIMEOUT"}, {}, REQUIRES_APPROVAL, "AVAILABILITY_UNVERIFIED"),
    ({"price_status": "API_ERROR", "total_excl_tax": None}, {}, REQUIRES_APPROVAL, "PRICE_UNVERIFIED"),
    ({"price_status": "UNVERIFIED", "total_excl_tax": None}, {"unverified_price_action": "BLOCK"}, BLOCKED, "PRICE_UNVERIFIED"),
    ({"allergen_overall": "UNKNOWN"}, {}, BLOCKED, "ALLERGEN_UNVERIFIED"),
    ({"allergen_overall": "UNKNOWN"}, {"unknown_allergen_action": "REQUIRE_APPROVAL"}, REQUIRES_APPROVAL, "ALLERGEN_UNVERIFIED"),
    ({"allergen_overall": "VIOLATION"}, {}, BLOCKED, "ALLERGEN_VIOLATION"),
    ({"claim_statuses": {"FREE_FROM_LACTOSE": "NAME_ONLY"}}, {}, REQUIRES_APPROVAL, "REQUIREMENT_UNVERIFIED"),
    ({"claim_statuses": {"FREE_FROM_LACTOSE": "NOT_MET"}}, {}, BLOCKED, "REQUIREMENT_NOT_MET"),
    ({"conversion_status": "ESTIMATED"}, {}, REQUIRES_APPROVAL, "CONVERSION_ESTIMATED"),
    ({"conversion_status": "CONFLICT", "quantity_sales_units": None}, {}, BLOCKED, "QUANTITY_UNVERIFIABLE"),
    ({"is_substitute": True}, {}, REQUIRES_APPROVAL, "SUBSTITUTION_REVIEW"),
    ({"is_substitute": True}, {"allow_substitutions": False}, BLOCKED, "SUBSTITUTION_NOT_ALLOWED"),
    ({"duplicate_of": "req_1"}, {}, BLOCKED, "DUPLICATE_REQUEST"),
    ({"budget_limit": 50.0}, {}, REQUIRES_APPROVAL, "BUDGET_EXCEEDED"),
    ({"quantity_sales_units": 1000}, {"max_quantity": 100}, REQUIRES_APPROVAL, "QUANTITY_LIMIT"),
    ({"lot_valid": False}, {}, BLOCKED, "LOT_SIZE_INVALID"),
    ({}, {"auto_approval_enabled": False}, REQUIRES_APPROVAL, "AUTO_APPROVAL_DISABLED"),
    ({"cutoff_passed": True}, {}, REQUIRES_APPROVAL, "CUTOFF_PASSED"),
])
def test_policy_rules(kw, policy_over, expected, code):
    r = evaluate({**POLICY, **policy_over}, ctx(**kw))
    assert r.decision == expected
    assert code in [x["code"] for x in r.reasons]


def test_blocked_wins_over_approval():
    r = evaluate(POLICY, ctx(total_excl_tax=900.0, availability_status="UNAVAILABLE"))
    assert r.decision == BLOCKED


def test_unverified_price_is_never_allowed():
    for action in ("REQUIRE_APPROVAL", "BLOCK"):
        r = evaluate({**POLICY, "unverified_price_action": action}, ctx(price_status="UNVERIFIED", total_excl_tax=None))
        assert r.decision != ALLOWED
