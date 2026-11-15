"""Seed organizations, demo users, agent identities and default policies (idempotent).

Two organizations are seeded so tenant isolation is demonstrable:
  org_default  "Demo Kitchen Group"   — the main demo tenant
  org_nordic   "Nordic Bistro Oy"     — a second tenant whose records must never be visible to the first

Demo users are seeded only when SEED_DEMO_USERS=true (default for local demo). Each user's record
visibility is explicit: read_scope OWN (own records only) or ORG (organization-wide).
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Agent, AgentKey, Organization, Policy, User
from app.security import (ADMIN, BUYER, DEFAULT_AGENT_ID, DEFAULT_AGENT_SCOPES, MANAGER, VIEWER, hash_agent_secret,
                          hash_password)

ORGS = [("org_default", "Demo Kitchen Group"), ("org_nordic", "Nordic Bistro Oy")]

# email, name, role, organization, read_scope
DEMO_USERS = [
    ("admin@aimo.local", "Alex Admin", ADMIN, "org_default", "ORG"),
    ("manager@aimo.local", "Maria Manager (Approver)", MANAGER, "org_default", "ORG"),
    ("buyer@aimo.local", "Ben Buyer", BUYER, "org_default", "OWN"),
    ("buyer2@aimo.local", "Bea Buyer (second buyer)", BUYER, "org_default", "OWN"),
    ("viewer@aimo.local", "Vera Viewer", VIEWER, "org_default", "ORG"),
    ("buyer@nordic.local", "Niko Buyer (Nordic Bistro)", BUYER, "org_nordic", "OWN"),
    ("manager@nordic.local", "Nora Manager (Nordic Bistro)", MANAGER, "org_nordic", "ORG"),
]


def seed(db: Session) -> None:
    s = get_settings()
    for oid, name in ORGS:
        if not db.get(Organization, oid):
            db.add(Organization(id=oid, name=name))
    db.flush()
    if s.seed_demo_users:
        pw = s.demo_password.get_secret_value()
        for email, name, role, org, scope in DEMO_USERS:
            if not db.query(User).filter(User.email == email).first():
                db.add(User(email=email, name=name, role=role, password_hash=hash_password(pw),
                            organization_id=org, read_scope=scope))
    db.flush()
    if not db.get(Agent, DEFAULT_AGENT_ID):
        buyer = db.query(User).filter(User.email == "buyer@aimo.local").first()
        db.add(Agent(id=DEFAULT_AGENT_ID, name="AIMO Procurement Agent 01", organization_id="org_default",
                     owner_user_id=buyer.id if buyer else None,
                     description="Default external purchasing agent for the demo tenant"))
    db.flush()
    _sync_bootstrap_key(db)
    for oid, name in ORGS:
        if not db.query(Policy).filter(Policy.organization_id == oid).first():
            db.add(Policy(name="Standard Procurement", max_order_value=500.0, updated_by="seed", organization_id=oid))
    db.commit()


def _sync_bootstrap_key(db: Session) -> None:
    """AGENT_API_KEY (if set) becomes a hashed, revocable key for the default agent — never stored in plaintext."""
    raw = get_settings().agent_api_key.get_secret_value().strip()
    prefix = f"bootstrap_{DEFAULT_AGENT_ID}"
    key = db.query(AgentKey).filter(AgentKey.prefix == prefix).first()
    if not raw:
        return
    digest = hash_agent_secret(raw)
    if key is None:
        db.add(AgentKey(agent_id=DEFAULT_AGENT_ID, prefix=prefix, key_hash=digest,
                        scopes_json=json.dumps(DEFAULT_AGENT_SCOPES + ["live:check"]),
                        label="Bootstrap key from AGENT_API_KEY (rotate in Agents & Access)", created_by="seed"))
    elif key.key_hash != digest and key.revoked_at is None:
        key.key_hash = digest
