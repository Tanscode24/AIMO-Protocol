"""Authentication, role-based access control and secret redaction.

Tokens are HMAC-SHA256 signed (stdlib only). Passwords use PBKDF2-SHA256.
The AI agent is a separate principal with role PROCUREMENT_AGENT, which can create
and decide procurement requests but can never approve, edit policies or change roles.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass

from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Agent, AgentKey, User
from app.services.sessions import resolve_session

ADMIN = "ADMIN"
MANAGER = "PROCUREMENT_MANAGER"
BUYER = "BUYER"
AGENT = "PROCUREMENT_AGENT"
VIEWER = "VIEWER"
ALL_ROLES = [ADMIN, MANAGER, BUYER, AGENT, VIEWER]

PERMISSIONS: dict[str, set[str]] = {
    "view": {ADMIN, MANAGER, BUYER, AGENT, VIEWER},
    "procure": {ADMIN, MANAGER, BUYER, AGENT},
    "live_check": {ADMIN, MANAGER, BUYER, AGENT},
    "approve": {ADMIN, MANAGER},
    "edit_policy": {ADMIN},
    "manage_identities": {ADMIN},
    "view_trust": {ADMIN, MANAGER, BUYER, VIEWER},
    "verify_audit": {ADMIN},
}

# Agents never get a permission by role alone: each permission also requires an explicit key scope.
AGENT_SCOPES = {
    "catalogue:read": {"view"},
    "procure:create": {"procure"},
    "procure:read": {"view"},
    "live:check": {"live_check"},
}
DEFAULT_AGENT_SCOPES = ["catalogue:read", "procure:create", "procure:read"]
SESSION_COOKIE = "aimo_session"
AGENT_KEY_PREFIX = "aimo_ak_"


@dataclass
class Principal:
    id: str
    name: str
    role: str
    kind: str  # USER | AGENT | SYSTEM
    email: str | None = None
    organization_id: str | None = None
    read_scope: str = "OWN"  # OWN | ORG
    scopes: tuple[str, ...] = ()
    key_id: str | None = None

    def can(self, permission: str) -> bool:
        if self.role not in PERMISSIONS.get(permission, set()):
            return False
        if self.kind == "AGENT":
            return any(permission in AGENT_SCOPES.get(sc, set()) for sc in self.scopes)
        return True


# ---------------------------------------------------------------- passwords
def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, _ = stored.split("$", 2)
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt), stored)


# Verified against when the account does not exist, so unknown and known emails cost the same PBKDF2 time.
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(16))


def burn_password_check(password: str) -> None:
    verify_password(password, _DUMMY_PASSWORD_HASH)


# ---------------------------------------------------------------- tokens
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def create_token(user_id: str, role: str, session_id: str) -> str:
    """Signed token bound to a server-side session. `role` is informational only and never trusted:
    the principal's role, organization and scope are always reloaded from the database."""
    s = get_settings()
    now = int(time.time())
    payload = {"sub": user_id, "sid": session_id, "role": role, "iat": now, "exp": now + s.token_ttl_minutes * 60}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(s.auth_secret.get_secret_value().encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(sig)}"


def decode_token(token: str) -> dict | None:
    s = get_settings()
    try:
        body, sig = token.split(".", 1)
        expected = hmac.new(s.auth_secret.get_secret_value().encode(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64(expected), sig):
            return None
        payload = json.loads(_unb64(body))
        if not isinstance(payload, dict) or payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


# ---------------------------------------------------------------- dependencies
DEFAULT_AGENT_ID = "aimo-procurement-agent-01"


def hash_agent_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def generate_agent_key() -> tuple[str, str, str]:
    """Returns (plaintext_key, prefix, sha256_hash). The plaintext is shown once and never stored."""
    prefix = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    full = f"{AGENT_KEY_PREFIX}{prefix}_{secret}"
    return full, prefix, hash_agent_secret(full)


def authenticate_agent_key(db: Session, presented: str) -> tuple[Agent, AgentKey] | None:
    """Per-agent credential lookup by public prefix + constant-time hash comparison."""
    candidates: list[AgentKey] = []
    if presented.startswith(AGENT_KEY_PREFIX):
        prefix = presented[len(AGENT_KEY_PREFIX):].split("_", 1)[0]
        k = db.query(AgentKey).filter(AgentKey.prefix == prefix).first()
        if k:
            candidates = [k]
    else:  # bootstrap key from AGENT_API_KEY (stored hashed like any other key)
        candidates = db.query(AgentKey).filter(AgentKey.prefix.like("bootstrap%")).all()
    digest = hash_agent_secret(presented)
    now = datetime.now(timezone.utc)
    for k in candidates:
        if not hmac.compare_digest(k.key_hash, digest):
            continue
        if k.revoked_at is not None:
            return None
        exp = k.expires_at
        if exp is not None and (exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)) < now:
            return None
        agent = db.get(Agent, k.agent_id)
        if not agent or not agent.active:
            return None
        k.last_used_at = now
        db.commit()
        return agent, k
    return None


def _user_principal(user: User) -> Principal:
    return Principal(id=user.id, name=user.name, role=user.role, kind="USER", email=user.email,
                     organization_id=user.organization_id, read_scope=user.read_scope or "OWN")


def set_session_cookie(response: Response, token: str) -> None:
    s = get_settings()
    # HttpOnly: not readable by JavaScript. Secure: mandatory in production (enforced at start-up).
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite=s.session_cookie_samesite,
                        secure=s.session_cookie_secure, max_age=s.token_ttl_minutes * 60, path="/")


def clear_session_cookie(response: Response) -> None:
    s = get_settings()
    # Same attributes as when set, so browsers reliably replace a Secure/SameSite cookie.
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite=s.session_cookie_samesite,
                           secure=s.session_cookie_secure)


def presented_token(request: Request, authorization: str | None) -> tuple[str | None, bool]:
    """(token, from_cookie). A bearer header takes precedence over the cookie."""
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip(), False
    cookie = request.cookies.get(SESSION_COOKIE)
    return (cookie, True) if cookie else (None, False)


def get_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_agent_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Principal:
    if x_agent_key:
        found = authenticate_agent_key(db, x_agent_key.strip())
        if not found:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid, revoked or expired agent credentials")
        agent, key = found
        return Principal(id=agent.id, name=agent.name, role=AGENT, kind="AGENT", organization_id=agent.organization_id,
                         read_scope="OWN", scopes=tuple(json.loads(key.scopes_json or "[]")), key_id=key.id)
    token, from_cookie = presented_token(request, authorization)
    if from_cookie:
        # CSRF defence for cookie sessions: state-changing requests must carry a custom header,
        # which browsers only send cross-origin after a CORS preflight that our allow-list controls.
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-requested-with") != "aimo-web":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing CSRF header for cookie-authenticated request")
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    # Server-side session check on EVERY request: revoked (logout), expired, pre-password-change or
    # deactivated sessions are rejected even though the token signature is still valid. Tokens issued
    # before sessions existed carry no `sid` and are rejected too.
    user = resolve_session(db, payload.get("sid"), payload.get("sub"))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session is invalid, expired or has been signed out")
    # Role, organization and scope are always read from the database, never trusted from the token or client.
    return _user_principal(user)


def require(permission: str):
    def _dep(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> Principal:
        if not principal.can(permission):
            from app.services import audit  # local import: audit depends on this module

            audit.record(db, "ACCESS_DENIED", principal, decision="DENIED",
                         reason=f"{principal.kind} {principal.name} ({principal.role}) lacks permission '{permission}'",
                         payload={"permission": permission, "scopes": list(principal.scopes)}, commit=True)
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"{'Agent scopes' if principal.kind == 'AGENT' else 'Role ' + principal.role} not permitted to perform '{permission}'",
            )
        return principal

    return _dep


# ---------------------------------------------------------------- redaction
SENSITIVE_KEYS = {"x-api-key", "api_key", "apikey", "authorization", "password", "token", "secret", "x-agent-key"}


def redact(obj):
    """Recursively remove secrets from structures before they are logged or audited."""
    secrets_list = get_settings().secret_values()
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if str(k).lower() in SENSITIVE_KEYS else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        out = obj
        for sv in secrets_list:
            if sv in out:
                out = out.replace(sv, "[REDACTED]")
        return out
    return obj


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        red = redact(msg)
        if red != msg:
            record.msg = red
            record.args = ()
        return True


def install_log_redaction() -> None:
    f = RedactingFilter()
    for name in ("", "uvicorn", "uvicorn.access", "uvicorn.error", "httpx", "app"):
        logging.getLogger(name).addFilter(f)
    for h in logging.getLogger().handlers:
        h.addFilter(f)
