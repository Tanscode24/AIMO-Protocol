"""Pydantic request/response schemas."""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Priority = Literal["price", "reliability", "sustainability", "availability"]


class ProcurementIntent(BaseModel):
    """Structured interpretation of a natural-language procurement request."""

    product_query: str = Field(description="Core product noun phrase used for catalogue search, e.g. 'milk'")
    display_query: str = Field(description="Product phrase as the user wrote it, e.g. 'lactose-free milk'")
    quantity: float | None = Field(default=None, gt=0)
    unit: Literal["L", "KG", "SALES_UNIT"] | None = None
    unit_text: str | None = None
    delivery_date: date | None = None
    delivery_date_text: str | None = None
    budget_limit: float | None = Field(default=None, gt=0)
    currency: str = "EUR"
    excluded_allergens: list[str] = Field(default_factory=list, description="GS1 allergen codes that must be absent")
    excluded_allergen_terms: list[str] = Field(default_factory=list)
    required_attributes: list[str] = Field(default_factory=list, description="Nutritional claim ids, e.g. FREE_FROM_LACTOSE")
    preferred_attributes: list[str] = Field(default_factory=list)
    priorities: list[Priority] = Field(default_factory=list)
    delivery_preferences: list[str] = Field(default_factory=list)
    sustainability_preference: bool = False
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    confirmation_required: list[str] = Field(
        default_factory=list,
        description="Assumptions that materially affect the purchase (quantity, unit, delivery date) and need confirmation")

    @field_validator("product_query")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("product_query must not be empty")
        return v[:200]


class ParseRequest(BaseModel):
    text: str = Field(min_length=3, max_length=2000)


class ProcurementCreate(BaseModel):
    text: str = Field(min_length=3, max_length=2000, description="Original natural-language request (always preserved)")
    simulate: Literal["timeout", "api_error"] | None = Field(default=None, description="Demo only: simulate a Valio API failure")
    auto_decide: bool = True
    intent_override: ProcurementIntent | None = Field(default=None, description="Structured intent; bypasses NL parsing")
    confirm_assumptions: bool = Field(default=False, description="Requester confirms parser assumptions about quantity/unit/date")


class DecideRequest(BaseModel):
    product_id: str | None = Field(default=None, max_length=20)


class ApprovalAction(BaseModel):
    reason: str = Field(default="", max_length=1000)


class LoginRequest(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)


class LiveCheckRequest(BaseModel):
    requested_date: date
    quantity: float | None = Field(default=None, gt=0)


class PolicyUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    max_order_value: float | None = Field(default=None, ge=0)
    max_quantity: float | None = Field(default=None, ge=0)
    clear_max_quantity: bool = False
    auto_approval_enabled: bool | None = None
    require_human_approval_above_limit: bool | None = None
    allowed_categories: list[str] | None = None
    blocked_categories: list[str] | None = None
    blocked_products: list[str] | None = None
    allow_substitutions: bool | None = None
    substitution_requires_approval: bool | None = None
    unknown_allergen_action: Literal["BLOCK", "REQUIRE_APPROVAL"] | None = None
    unverified_price_action: Literal["BLOCK", "REQUIRE_APPROVAL"] | None = None
    unverified_availability_action: Literal["BLOCK", "REQUIRE_APPROVAL"] | None = None
    terminal_quantity_action: Literal["ALLOW", "REQUIRE_APPROVAL", "BLOCK"] | None = None
    unverified_conversion_action: Literal["BLOCK", "REQUIRE_APPROVAL"] | None = None
    allow_late_delivery: bool | None = None
    duplicate_window_minutes: int | None = Field(default=None, ge=0, le=10080)
    weight_price: float | None = Field(default=None, ge=0, le=1)
    weight_reliability: float | None = Field(default=None, ge=0, le=1)
    weight_sustainability: float | None = Field(default=None, ge=0, le=1)
    weight_match: float | None = Field(default=None, ge=0, le=1)
