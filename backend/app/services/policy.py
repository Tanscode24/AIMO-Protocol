"""Deterministic procurement policy engine.

Pure function: evaluate(policy_snapshot, context) -> PolicyResult.
Decisions: ALLOWED | REQUIRES_APPROVAL | BLOCKED. BLOCKED wins over REQUIRES_APPROVAL.
Only the backend calls this. Neither the LLM, the agent, nor the frontend can change its output.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

ALLOWED, REQUIRES_APPROVAL, BLOCKED = "ALLOWED", "REQUIRES_APPROVAL", "BLOCKED"
_ORDER = {ALLOWED: 0, REQUIRES_APPROVAL: 1, BLOCKED: 2}


def policy_snapshot(p) -> dict:
    return {
        "id": p.id, "name": p.name, "version": p.version,
        "max_order_value": p.max_order_value, "max_quantity": p.max_quantity,
        "auto_approval_enabled": p.auto_approval_enabled,
        "require_human_approval_above_limit": p.require_human_approval_above_limit,
        "allowed_categories": json.loads(p.allowed_categories_json or "[]"),
        "blocked_categories": json.loads(p.blocked_categories_json or "[]"),
        "blocked_products": json.loads(p.blocked_products_json or "[]"),
        "allow_substitutions": p.allow_substitutions,
        "substitution_requires_approval": p.substitution_requires_approval,
        "unknown_allergen_action": p.unknown_allergen_action,
        "unverified_price_action": p.unverified_price_action,
        "unverified_availability_action": p.unverified_availability_action,
        "terminal_quantity_action": p.terminal_quantity_action,
        "unverified_conversion_action": p.unverified_conversion_action,
        "allow_late_delivery": p.allow_late_delivery,
        "duplicate_window_minutes": p.duplicate_window_minutes,
        "weights": {"price": p.weight_price, "reliability": p.weight_reliability,
                    "sustainability": p.weight_sustainability, "match": p.weight_match},
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        "updated_by": p.updated_by,
    }


@dataclass
class DecisionContext:
    product_id: str
    category: str
    quantity_sales_units: float | None
    conversion_status: str  # VERIFIED | ESTIMATED | CONFLICT | UNKNOWN
    price_status: str  # VERIFIED | UNVERIFIED | API_ERROR | TIMEOUT
    total_excl_tax: float | None
    availability_status: str
    cutoff_passed: bool = False
    allergen_overall: str = "NOT_REQUIRED"  # NOT_REQUIRED | VERIFIED_SAFE | UNKNOWN | VIOLATION
    claim_statuses: dict[str, str] = field(default_factory=dict)  # claim -> VERIFIED | NAME_ONLY | NOT_MET
    is_substitute: bool = False
    budget_limit: float | None = None
    duplicate_of: str | None = None
    lot_valid: bool = True
    unconfirmed_assumptions: list[str] = field(default_factory=list)


@dataclass
class PolicyResult:
    decision: str
    reasons: list[dict[str, Any]]
    policy_id: str
    policy_version: int

    @property
    def approval_codes(self) -> list[str]:
        return [r["code"] for r in self.reasons if r["outcome"] == REQUIRES_APPROVAL]

    def to_dict(self) -> dict:
        return {"decision": self.decision, "reasons": self.reasons, "policy_id": self.policy_id,
                "policy_version": self.policy_version}


def _action(setting: str) -> str:
    return {"BLOCK": BLOCKED, "REQUIRE_APPROVAL": REQUIRES_APPROVAL, "ALLOW": ALLOWED}.get(setting, REQUIRES_APPROVAL)


def evaluate(policy: dict, ctx: DecisionContext) -> PolicyResult:
    reasons: list[dict] = []

    def add(code: str, outcome: str, message: str) -> None:
        reasons.append({"code": code, "outcome": outcome, "message": message})

    # Duplicate protection
    if ctx.duplicate_of:
        add("DUPLICATE_REQUEST", BLOCKED, f"Identical request {ctx.duplicate_of} is already active (pending approval or proposal ready)")

    # Product / category permissions
    if ctx.product_id in policy["blocked_products"]:
        add("PRODUCT_BLOCKED", BLOCKED, f"Product {ctx.product_id} is explicitly blocked by policy")
    if ctx.category in policy["blocked_categories"]:
        add("CATEGORY_BLOCKED", BLOCKED, f"Category {ctx.category} is blocked by policy")
    if policy["allowed_categories"] and ctx.category not in policy["allowed_categories"]:
        add("CATEGORY_NOT_ALLOWED", BLOCKED, f"Category {ctx.category} is not in the allowed category list")

    # Allergens and dietary requirements
    if ctx.allergen_overall == "VIOLATION":
        add("ALLERGEN_VIOLATION", BLOCKED, "Product contains or may contain an excluded allergen")
    elif ctx.allergen_overall == "UNKNOWN":
        add("ALLERGEN_UNVERIFIED", _action(policy["unknown_allergen_action"]),
            "Allergen exclusion cannot be verified from product data (not declared FREE_FROM)")
    for claim, st in ctx.claim_statuses.items():
        if st == "NOT_MET":
            add("REQUIREMENT_NOT_MET", BLOCKED, f"Required attribute {claim} is not met")
        elif st == "NAME_ONLY":
            add("REQUIREMENT_UNVERIFIED", REQUIRES_APPROVAL, f"{claim} only indicated by product name, not a declared claim")

    # Units and lot sizes
    if ctx.conversion_status in ("CONFLICT", "UNKNOWN") or ctx.quantity_sales_units is None:
        add("QUANTITY_UNVERIFIABLE", BLOCKED, "Requested quantity cannot be converted into verified sales units")
    elif ctx.conversion_status == "ESTIMATED":
        add("CONVERSION_ESTIMATED", _action(policy["unverified_conversion_action"]),
            "Unit conversion is estimated, not declared in catalogue packaging data")
    if not ctx.lot_valid:
        add("LOT_SIZE_INVALID", BLOCKED, "Quantity is not a multiple of allowedLotSize")
    if policy["max_quantity"] is not None and ctx.quantity_sales_units is not None and ctx.quantity_sales_units > policy["max_quantity"]:
        add("QUANTITY_LIMIT", REQUIRES_APPROVAL,
            f"Quantity {ctx.quantity_sales_units:g} exceeds autonomous limit {policy['max_quantity']:g}")

    # Availability
    av = ctx.availability_status
    if av == "UNAVAILABLE":
        add("UNAVAILABLE", BLOCKED, "Product cannot be delivered (sales block, no schedule lines or zero stock)")
    elif av == "PARTIAL":
        add("INSUFFICIENT_STOCK", BLOCKED, "Available quantity is below the requested quantity")
    elif av == "LATE":
        add("LATE_DELIVERY", REQUIRES_APPROVAL if policy["allow_late_delivery"] else BLOCKED,
            "Earliest available date is after the requested delivery date")
    elif av == "AVAILABLE_QTY_UNCONFIRMED":
        add("QUANTITY_UNCONFIRMED", _action(policy["terminal_quantity_action"]),
            "Delivery date confirmed but available quantity is not confirmed (Terminal 999999 placeholder or "
            "unconfirmed Stock sentinel); a human must accept the quantity risk")
    elif av in ("UNKNOWN", "API_ERROR", "TIMEOUT"):
        add("AVAILABILITY_UNVERIFIED", _action(policy["unverified_availability_action"]),
            f"Availability could not be verified ({av}); never treated as available")
    if ctx.cutoff_passed:
        add("CUTOFF_PASSED", REQUIRES_APPROVAL, "Order closing time for this delivery has already passed")

    # Price and spend
    if ctx.price_status != "VERIFIED":
        add("PRICE_UNVERIFIED", _action(policy["unverified_price_action"]),
            f"Price could not be verified ({ctx.price_status}); automatic purchase is prohibited")
    elif ctx.total_excl_tax is not None:
        if ctx.total_excl_tax > policy["max_order_value"]:
            add("SPEND_LIMIT", REQUIRES_APPROVAL if policy["require_human_approval_above_limit"] else BLOCKED,
                f"Order value {ctx.total_excl_tax:.2f} exceeds autonomous limit {policy['max_order_value']:.2f}")
        if ctx.budget_limit is not None and ctx.total_excl_tax > ctx.budget_limit:
            add("BUDGET_EXCEEDED", REQUIRES_APPROVAL,
                f"Order value {ctx.total_excl_tax:.2f} exceeds the request budget {ctx.budget_limit:.2f}")

    # Substitution
    if ctx.is_substitute:
        if not policy["allow_substitutions"]:
            add("SUBSTITUTION_NOT_ALLOWED", BLOCKED, "Policy does not allow substitutions")
        elif policy["substitution_requires_approval"]:
            add("SUBSTITUTION_REVIEW", REQUIRES_APPROVAL, "Substitute product requires human confirmation")

    if ctx.unconfirmed_assumptions:
        add("ASSUMPTION_UNCONFIRMED", REQUIRES_APPROVAL,
            "Purchasing-relevant assumption(s) not confirmed by the requester: " + "; ".join(ctx.unconfirmed_assumptions))

    if not policy["auto_approval_enabled"]:
        add("AUTO_APPROVAL_DISABLED", REQUIRES_APPROVAL, "Autonomous purchasing is disabled; every decision needs approval")

    decision = ALLOWED
    for r in reasons:
        if _ORDER[r["outcome"]] > _ORDER[decision]:
            decision = r["outcome"]
    if decision == ALLOWED:
        add("WITHIN_POLICY", ALLOWED, "All hard constraints verified and order is within autonomous limits")
    return PolicyResult(decision=decision, reasons=reasons, policy_id=policy["id"], policy_version=policy["version"])
