"""Administration: users, agent identities, per-agent API keys and audit-chain verification.

All operations are restricted to the administrator's own organization. Admins cannot write
verification evidence or delete audit events — there are no endpoints for either.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Agent, AgentKey, User, new_id, utcnow
from app.security import (ADMIN, AGENT_SCOPES, BUYER, MANAGER, VIEWER, Principal, generate_agent_key, hash_password,
                          require)
from app.services import audit, sessions

router = APIRouter(prefix="/api/admin", tags=["Administration"])


class UserCreate(BaseModel):
    email: str = Field(max_length=200, pattern=r"^[^@\s]+@[^@\s]+$")
    name: str = Field(min_length=1, max_length=200)
    role: Literal["ADMIN", "PROCUREMENT_MANAGER", "BUYER", "VIEWER"]
    read_scope: Literal["OWN", "ORG"] = "OWN"
    password: str = Field(min_length=10, max_length=200)


class UserUpdate(BaseModel):
    role: Literal["ADMIN", "PROCUREMENT_MANAGER", "BUYER", "VIEWER"] | None = None
    read_scope: Literal["OWN", "ORG"] | None = None
    active: bool | None = None


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    owner_user_id: str | None = None


class AgentUpdate(BaseModel):
    active: bool | None = None
    description: str | None = Field(default=None, max_length=1000)


class KeyCreate(BaseModel):
    scopes: list[str] = Field(default_factory=lambda: ["catalogue:read", "procure:create", "procure:read"])
    label: str | None = Field(default=None, max_length=200)
    expires_in_days: int | None = Field(default=90, ge=1, le=730)


def _user(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "read_scope": u.read_scope,
            "active": u.active, "organization_id": u.organization_id,
            "created_at": u.created_at.isoformat() if u.created_at else None}


def _key(k: AgentKey) -> dict:
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    return {"id": k.id, "prefix": k.prefix, "scopes": json.loads(k.scopes_json or "[]"), "label": k.label,
            "created_at": iso(k.created_at), "created_by": k.created_by, "expires_at": iso(k.expires_at),
            "revoked_at": iso(k.revoked_at), "last_used_at": iso(k.last_used_at),
            "status": "REVOKED" if k.revoked_at else "ACTIVE"}


def _agent(db: Session, a: Agent) -> dict:
    keys = db.query(AgentKey).filter(AgentKey.agent_id == a.id).order_by(AgentKey.created_at.desc()).all()
    return {"id": a.id, "name": a.name, "description": a.description, "active": a.active,
            "organization_id": a.organization_id, "owner_user_id": a.owner_user_id, "keys": [_key(k) for k in keys]}


def _get_agent(db: Session, principal: Principal, agent_id: str) -> Agent:
    a = db.get(Agent, agent_id)
    if not a or a.organization_id != principal.organization_id:
        raise HTTPException(404, "Agent not found")
    return a


# ------------------------------------------------------------------ users
@router.get("/users")
def list_users(db: Session = Depends(get_db), principal: Principal = Depends(require("manage_identities"))):
    return [_user(u) for u in db.query(User).filter(User.organization_id == principal.organization_id).order_by(User.email)]


@router.post("/users", status_code=201)
def create_user(body: UserCreate, db: Session = Depends(get_db), principal: Principal = Depends(require("manage_identities"))):
    if db.query(User).filter(User.email == body.email.lower()).first():
        raise HTTPException(409, "A user with this email already exists")
    if body.role == BUYER and body.read_scope == "ORG":
        raise HTTPException(422, "Buyers are limited to their own records (read_scope OWN)")
    u = User(email=body.email.lower(), name=body.name, role=body.role, read_scope=body.read_scope,
             password_hash=hash_password(body.password), organization_id=principal.organization_id)
    db.add(u)
    db.flush()
    audit.record(db, "USER_CREATED", principal, reason=f"Created {u.email} as {u.role} ({u.read_scope})", commit=True)
    return _user(u)


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: UserUpdate, db: Session = Depends(get_db),
                principal: Principal = Depends(require("manage_identities"))):
    u = db.get(User, user_id)
    if not u or u.organization_id != principal.organization_id:
        raise HTTPException(404, "User not found")
    if u.id == principal.id and (body.role not in (None, ADMIN) or body.active is False):
        raise HTTPException(409, "You cannot demote or deactivate your own account")
    changes = body.model_dump(exclude_none=True)
    new_role = changes.get("role", u.role)
    new_scope = changes.get("read_scope", u.read_scope)
    if new_role == BUYER and new_scope == "ORG":
        raise HTTPException(422, "Buyers are limited to their own records (read_scope OWN)")
    before = _user(u)
    for k, v in changes.items():
        setattr(u, k, v)
    if changes.get("active") is False:
        # Deactivation ends every live session; reactivation does not resurrect them.
        sessions.revoke_user_sessions(db, u.id, sessions.REVOKED_DEACTIVATED, commit=False)
    audit.record(db, "USER_UPDATED", principal, reason=f"Updated {u.email}",
                 payload={"before": {k: before[k] for k in changes}, "after": changes}, commit=True)
    return _user(u)


# ------------------------------------------------------------------ agents & keys
@router.get("/agents")
def list_agents(db: Session = Depends(get_db), principal: Principal = Depends(require("manage_identities"))):
    return [_agent(db, a) for a in db.query(Agent).filter(Agent.organization_id == principal.organization_id)]


@router.post("/agents", status_code=201)
def create_agent(body: AgentCreate, db: Session = Depends(get_db), principal: Principal = Depends(require("manage_identities"))):
    if body.owner_user_id:
        owner = db.get(User, body.owner_user_id)
        if not owner or owner.organization_id != principal.organization_id:
            raise HTTPException(422, "Owner must be a user of your organization")
    a = Agent(id=new_id("agt"), name=body.name, description=body.description, owner_user_id=body.owner_user_id,
              organization_id=principal.organization_id)
    db.add(a)
    db.flush()
    audit.record(db, "AGENT_CREATED", principal, reason=f"Agent {a.id} '{a.name}' created", commit=True)
    return _agent(db, a)


@router.patch("/agents/{agent_id}")
def update_agent(agent_id: str, body: AgentUpdate, db: Session = Depends(get_db),
                 principal: Principal = Depends(require("manage_identities"))):
    a = _get_agent(db, principal, agent_id)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(a, k, v)
    audit.record(db, "AGENT_UPDATED", principal,
                 reason=f"Agent {a.id} updated: {body.model_dump(exclude_none=True)}", commit=True)
    return _agent(db, a)


def _issue(db: Session, principal: Principal, agent: Agent, body: KeyCreate) -> dict:
    bad = [s for s in body.scopes if s not in AGENT_SCOPES]
    if bad or not body.scopes:
        raise HTTPException(422, f"Unknown or empty scopes {bad}; allowed: {sorted(AGENT_SCOPES)}")
    plaintext, prefix, digest = generate_agent_key()
    k = AgentKey(agent_id=agent.id, prefix=prefix, key_hash=digest, scopes_json=json.dumps(sorted(set(body.scopes))),
                 label=body.label, created_by=principal.name,
                 expires_at=utcnow() + timedelta(days=body.expires_in_days) if body.expires_in_days else None)
    db.add(k)
    db.flush()
    audit.record(db, "AGENT_KEY_ISSUED", principal, reason=f"Key {k.id} ({prefix}) issued to agent {agent.id}",
                 payload={"scopes": body.scopes, "expires_at": k.expires_at}, commit=True)
    return {**_key(k), "api_key": plaintext,
            "warning": "Store this key now. Only its hash is kept; it cannot be shown again."}


@router.post("/agents/{agent_id}/keys", status_code=201)
def issue_key(agent_id: str, body: KeyCreate, db: Session = Depends(get_db),
              principal: Principal = Depends(require("manage_identities"))):
    return _issue(db, principal, _get_agent(db, principal, agent_id), body)


@router.post("/agents/{agent_id}/keys/{key_id}/revoke")
def revoke_key(agent_id: str, key_id: str, db: Session = Depends(get_db),
               principal: Principal = Depends(require("manage_identities"))):
    a = _get_agent(db, principal, agent_id)
    k = db.get(AgentKey, key_id)
    if not k or k.agent_id != a.id:
        raise HTTPException(404, "Key not found")
    if k.revoked_at is None:
        k.revoked_at = utcnow()
        audit.record(db, "AGENT_KEY_REVOKED", principal, reason=f"Key {k.id} ({k.prefix}) of agent {a.id} revoked", commit=True)
    return _key(k)


@router.post("/agents/{agent_id}/keys/{key_id}/rotate", status_code=201)
def rotate_key(agent_id: str, key_id: str, db: Session = Depends(get_db),
               principal: Principal = Depends(require("manage_identities"))):
    """Issue a replacement key with the same scopes and revoke the old one."""
    a = _get_agent(db, principal, agent_id)
    old = db.get(AgentKey, key_id)
    if not old or old.agent_id != a.id:
        raise HTTPException(404, "Key not found")
    new = _issue(db, principal, a, KeyCreate(scopes=json.loads(old.scopes_json), label=f"Rotation of {old.prefix}"))
    if old.revoked_at is None:
        old.revoked_at = utcnow()
        audit.record(db, "AGENT_KEY_REVOKED", principal, reason=f"Key {old.id} revoked by rotation", commit=True)
    return new


# ------------------------------------------------------------------ audit integrity
@router.get("/audit/verify")
def verify_audit(db: Session = Depends(get_db), principal: Principal = Depends(require("verify_audit"))):
    result = audit.verify_chain(db)
    audit.record(db, "AUDIT_CHAIN_VERIFIED", principal, decision="VALID" if result["ok"] else "BROKEN",
                 reason=result.get("problem") or f"{result['events_checked']} events verified", commit=True)
    return result


ALL_ROLES_FOR_UI = [ADMIN, MANAGER, BUYER, VIEWER]
