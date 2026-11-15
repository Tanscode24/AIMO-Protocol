"""External autonomous-agent gateway — /api/agent/v1.

Authentication: `X-Agent-Key: aimo_ak_<prefix>_<secret>` (per-agent, hashed at rest, revocable, scoped).
Every call: correlation ID (X-Correlation-ID in/out), per-agent rate limit shared by all worker processes
(429 + Retry-After; see app/services/rate_limit.py),
idempotency for writes (Idempotency-Key, namespaced per organization and agent), audit events.
Agents can only see the requests they created, within their organization.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.schemas.agent import SCHEMAS, AgentIdentity, AgentProcureRequest, ErrorResponse, ProcurementDecisionV1, json_schema
from app.security import Principal, get_principal
from app.services import access, audit, orchestrator
from app.services.rate_limit import RateLimiter
from app.services.search import search_catalogue

router = APIRouter(prefix="/api/agent/v1", tags=["Agent API v1"])

ERRORS = {401: {"model": ErrorResponse, "description": "Missing, invalid, revoked or expired agent key"},
          403: {"model": ErrorResponse, "description": "Key lacks the required scope"},
          404: {"model": ErrorResponse, "description": "Not found or not visible to this agent"},
          422: {"model": ErrorResponse, "description": "Request body failed validation"},
          429: {"model": ErrorResponse, "description": "Per-agent rate limit exceeded (see Retry-After)"}}


limiter = RateLimiter()


def agent_access(permission: str):
    def _dep(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> Principal:
        if not principal.can(permission):
            audit.record(db, "ACCESS_DENIED", principal, decision="DENIED",
                         reason=f"{principal.kind} {principal.name} lacks '{permission}' on the agent gateway",
                         payload={"scopes": list(principal.scopes)}, commit=True)
            raise HTTPException(403, f"Not permitted: '{permission}' (agent scopes: {', '.join(principal.scopes) or 'none'})")
        s = get_settings()
        limit = s.agent_rate_limit_per_minute
        retry = limiter.check(principal.id, limit, db=db, backend=s.agent_rate_limit_backend)
        if retry is not None:
            audit.record(db, "AGENT_RATE_LIMITED", principal, decision="RATE_LIMITED",
                         reason=f"More than {limit} requests/minute", commit=True)
            raise HTTPException(429, f"Rate limit of {limit} requests per minute exceeded", headers={"Retry-After": str(retry)})
        return principal

    return _dep


@router.get("/me", response_model=AgentIdentity, responses=ERRORS)
def whoami(principal: Principal = Depends(agent_access("view"))):
    """Identity, organization and scopes of the calling credential."""
    return AgentIdentity(agent_id=principal.id, name=principal.name, organization_id=principal.organization_id,
                         scopes=list(principal.scopes), key_id=principal.key_id,
                         rate_limit_per_minute=get_settings().agent_rate_limit_per_minute)


@router.post("/procure", response_model=ProcurementDecisionV1, response_model_by_alias=True, responses=ERRORS)
def procure(body: AgentProcureRequest, db: Session = Depends(get_db),
            principal: Principal = Depends(agent_access("procure")),
            idempotency_key: str | None = Header(default=None, max_length=120)):
    """Submit a procurement request. Returns the full decision document. Retrying with the same
    Idempotency-Key returns the original decision instead of creating a duplicate request."""
    req = orchestrator.run_procurement(db, body.text, principal, simulate=body.simulate, auto_decide=True,
                                       intent_override=body.intent_override, channel="AGENT_API",
                                       idempotency_key=access.scoped_idempotency_key(principal, idempotency_key),
                                       confirm_assumptions=body.confirm_assumptions)
    return orchestrator.decision_document(db, req)


@router.get("/decisions/{request_id}", response_model=ProcurementDecisionV1, response_model_by_alias=True, responses=ERRORS)
def decision(request_id: str, db: Session = Depends(get_db), principal: Principal = Depends(agent_access("view"))):
    return orchestrator.decision_document(db, access.get_request_for(db, principal, request_id))


@router.get("/catalogue/search", responses=ERRORS)
def catalogue_search(q: str = Query(..., min_length=1, max_length=200), page: int = Query(1, ge=1),
                     page_size: int = Query(20, ge=1, le=50), db: Session = Depends(get_db),
                     _: Principal = Depends(agent_access("view"))):
    """Search the official catalogue (static data only; no live price calls)."""
    return search_catalogue(db, q, page=page, page_size=page_size)


@router.get("/schemas")
def list_schemas():
    """Published JSON Schema artifacts (public, no authentication)."""
    return {"schemas": [{"name": n, "url": f"/api/agent/v1/schemas/{n}"} for n in SCHEMAS]}


@router.get("/schemas/{name}")
def get_schema(name: str):
    if name not in SCHEMAS:
        raise HTTPException(404, "Schema not found")
    return json_schema(name)
