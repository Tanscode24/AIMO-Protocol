"""SQLAlchemy ORM models."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


DEFAULT_ORG = "org_default"


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("usr"))
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(40))
    password_hash: Mapped[str] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    organization_id: Mapped[str] = mapped_column(String(40), index=True, default="org_default")
    # Record visibility: OWN = only records the user created; ORG = all records of the user's organization
    read_scope: Mapped[str] = mapped_column(String(10), default="OWN")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthSession(Base):
    """Server-side record of a user login session. Every session-bound token carries its id (`sid`) and is
    accepted only while this row is active, unexpired, and bound to the user's current password hash."""

    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # Fingerprint of the password hash at login: a password change invalidates every earlier session.
    password_fingerprint: Mapped[str] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(200), nullable=True)


class LoginAttempt(Base):
    """One row per in-flight or failed login attempt, used for brute-force throttling. Account and source
    are stored only as keyed HMAC digests (no plaintext email or IP). Successful logins delete their rows."""

    __tablename__ = "login_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_key: Mapped[str] = mapped_column(String(64), index=True)
    source_key: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[float] = mapped_column(Float, index=True)  # epoch seconds


class RateLimitHit(Base):
    """One row per accepted request in the current window, for limits shared by every worker process."""

    __tablename__ = "rate_limit_hits"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bucket: Mapped[str] = mapped_column(String(120), index=True)
    ts: Mapped[float] = mapped_column(Float, index=True)  # epoch seconds


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(40), default="PROCUREMENT_AGENT")
    owner_user_id: Mapped[str | None] = mapped_column(String(40), ForeignKey("users.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    organization_id: Mapped[str] = mapped_column(String(40), index=True, default="org_default")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # product code, always a string
    name_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    name_fi: Mapped[str | None] = mapped_column(Text, nullable=True)
    name_sv: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    vendor_name: Mapped[str] = mapped_column(String(200), index=True)
    category: Mapped[str] = mapped_column(String(20), index=True)
    sales_unit: Mapped[str] = mapped_column(String(10))
    base_unit: Mapped[str] = mapped_column(String(10))
    allowed_lot_size: Mapped[float] = mapped_column(Float)
    temperature_condition: Mapped[int | None] = mapped_column(Integer, nullable=True)
    country_of_origin: Mapped[str | None] = mapped_column(String(10), nullable=True)
    gtin: Mapped[str | None] = mapped_column(String(30), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_ingredients: Mapped[str | None] = mapped_column(Text, nullable=True)
    units_json: Mapped[str] = mapped_column(Text, default="[]")
    unit_conversions_json: Mapped[str] = mapped_column(Text, default="[]")
    allergens_json: Mapped[str] = mapped_column(Text, default="[]")  # [{code, relation}] relation CONTAINS|MAY_CONTAIN
    non_allergens_json: Mapped[str] = mapped_column(Text, default="[]")  # [code] relation FREE_FROM
    claims_json: Mapped[str] = mapped_column(Text, default="[]")  # nutritionalClaim ids
    allergen_data_status: Mapped[str] = mapped_column(String(20), index=True)  # DECLARED | UNKNOWN
    substitutions_json: Mapped[str] = mapped_column(Text, default="[]")
    deposits_json: Mapped[str] = mapped_column(Text, default="[]")
    vat_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    synkka_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    synkka_last_updated: Mapped[str | None] = mapped_column(String(40), nullable=True)
    completeness: Mapped[float] = mapped_column(Float, default=0.0)
    missing_fields_json: Mapped[str] = mapped_column(Text, default="[]")
    reliability: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbon_kg_per_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_json: Mapped[str] = mapped_column(Text)


class AgentKey(Base):
    """Per-agent API credential. Only a SHA-256 hash of the secret is stored."""

    __tablename__ = "agent_keys"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("key"))
    agent_id: Mapped[str] = mapped_column(String(60), ForeignKey("agents.id"), index=True)
    prefix: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    scopes_json: Mapped[str] = mapped_column(Text, default="[]")
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IntegrationEvent(Base):
    """Outcome of every call to the Valio API (for the Trust Center; never contains secrets)."""

    __tablename__ = "integration_events"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("int"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    operation: Mapped[str] = mapped_column(String(30), index=True)  # ping | price | availability
    status: Mapped[str] = mapped_column(String(30))  # OK | API_ERROR | TIMEOUT | AUTH_ERROR | RATE_LIMITED | NOT_CONFIGURED
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    products: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Policy(Base):
    __tablename__ = "policies"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("pol"))
    organization_id: Mapped[str] = mapped_column(String(40), index=True, default="org_default")
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    max_order_value: Mapped[float] = mapped_column(Float, default=500.0)
    max_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    auto_approval_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    require_human_approval_above_limit: Mapped[bool] = mapped_column(Boolean, default=True)
    allowed_categories_json: Mapped[str] = mapped_column(Text, default="[]")
    blocked_categories_json: Mapped[str] = mapped_column(Text, default="[]")
    blocked_products_json: Mapped[str] = mapped_column(Text, default="[]")
    allow_substitutions: Mapped[bool] = mapped_column(Boolean, default=True)
    substitution_requires_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    unknown_allergen_action: Mapped[str] = mapped_column(String(30), default="BLOCK")
    unverified_price_action: Mapped[str] = mapped_column(String(30), default="REQUIRE_APPROVAL")
    unverified_availability_action: Mapped[str] = mapped_column(String(30), default="REQUIRE_APPROVAL")
    terminal_quantity_action: Mapped[str] = mapped_column(String(30), default="REQUIRE_APPROVAL")
    unverified_conversion_action: Mapped[str] = mapped_column(String(30), default="REQUIRE_APPROVAL")
    allow_late_delivery: Mapped[bool] = mapped_column(Boolean, default=False)
    duplicate_window_minutes: Mapped[int] = mapped_column(Integer, default=60)
    weight_price: Mapped[float] = mapped_column(Float, default=0.40)
    weight_reliability: Mapped[float] = mapped_column(Float, default=0.30)
    weight_sustainability: Mapped[float] = mapped_column(Float, default=0.20)
    weight_match: Mapped[float] = mapped_column(Float, default=0.10)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(200), nullable=True)


class ProcurementRequest(Base):
    __tablename__ = "procurement_requests"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("req"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    created_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_by_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    channel: Mapped[str] = mapped_column(String(20), default="WORKSPACE")  # WORKSPACE | AGENT_API
    raw_text: Mapped[str] = mapped_column(Text)
    parsed_json: Mapped[str] = mapped_column(Text, default="{}")
    parser_source: Mapped[str] = mapped_column(String(40), default="RULES")
    fingerprint: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(40), default="ANALYZED", index=True)
    decision: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    decision_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    decision_reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    policy_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected_product_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    selected_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_total_excl_tax: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_total_incl_tax: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    delivery_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    simulate: Mapped[str | None] = mapped_column(String(30), nullable=True)
    live_api_used: Mapped[bool] = mapped_column(Boolean, default=False)
    summary_json: Mapped[str] = mapped_column(Text, default="{}")
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True)
    organization_id: Mapped[str] = mapped_column(String(40), index=True, default="org_default")
    overrides_json: Mapped[str] = mapped_column(Text, default="{}")
    confirmed_assumptions: Mapped[bool] = mapped_column(Boolean, default=False)
    correlation_id: Mapped[str | None] = mapped_column(String(80), nullable=True)


class Recommendation(Base):
    __tablename__ = "recommendations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("rec"))
    request_id: Mapped[str] = mapped_column(String(40), ForeignKey("procurement_requests.id"), index=True)
    product_id: Mapped[str] = mapped_column(String(20), index=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    tier: Mapped[str | None] = mapped_column(String(30), nullable=True)
    label: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="CATALOGUE_SEARCH")
    substitute_for: Mapped[str | None] = mapped_column(String(20), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    data_json: Mapped[str] = mapped_column(Text, default="{}")  # full candidate evaluation


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("apr"))
    request_id: Mapped[str] = mapped_column(String(40), ForeignKey("procurement_requests.id"), index=True)
    product_id: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default="PENDING", index=True)
    reason_codes_json: Mapped[str] = mapped_column(Text, default="[]")
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    decided_by_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    previous_state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    new_state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    revalidation_json: Mapped[str] = mapped_column(Text, default="{}")


class Order(Base):
    """Either an internal procurement PROPOSAL or (never, with the current API) a SUPPLIER_ORDER."""

    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("prp"))
    request_id: Mapped[str] = mapped_column(String(40), ForeignKey("procurement_requests.id"), index=True)
    approval_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kind: Mapped[str] = mapped_column(String(30), default="PROPOSAL")
    status: Mapped[str] = mapped_column(String(60), default="PROPOSAL_READY_NOT_SUBMITTED")
    submitted_to_supplier: Mapped[bool] = mapped_column(Boolean, default=False)
    supplier_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    policy_decision: Mapped[str] = mapped_column(String(30))
    approval_status: Mapped[str] = mapped_column(String(30), default="NOT_REQUIRED")
    total_excl_tax: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_incl_tax: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    delivery_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    lines_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    organization_id: Mapped[str] = mapped_column(String(40), index=True, default="org_default")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("evt"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor_type: Mapped[str] = mapped_column(String(20))  # USER | AGENT | SYSTEM
    actor_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    actor_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    product_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    organization_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Tamper-evidence: SHA-256 hash chain over (seq, prev_hash, canonical event content)
    seq: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(100))
    result_ref: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
