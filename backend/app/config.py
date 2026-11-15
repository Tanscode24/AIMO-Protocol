"""Application configuration loaded from backend/.env.

Secrets are held in SecretStr so they never appear in repr(), logs or API responses.
"""
from __future__ import annotations

import logging
import math
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent

log = logging.getLogger("app.config")

# Environments in which source-visible development defaults are tolerated. ANY other APP_ENV value
# (production, prod, staging, a typo, ...) is treated as production-grade and validated fail-closed.
NON_PRODUCTION_ENVS = frozenset({"local", "development", "dev", "demo", "test", "testing", "ci"})

DEV_AUTH_SECRET = "change-me-local-development-secret"
DEV_DEMO_PASSWORD = "aimo-demo-2026"
DEV_AGENT_API_KEY = "aimo-agent-local-key"

# Known placeholder / published secrets that must never sign production tokens.
KNOWN_PLACEHOLDER_SECRETS = frozenset({
    DEV_AUTH_SECRET, "change-me-to-a-long-random-string", "test-auth-secret-abcdef",
    "changeme", "change-me", "secret", "password", "auth_secret", "your-secret-here",
})
PLACEHOLDER_MARKERS = ("change-me", "changeme", "change_me", "placeholder", "example", "your-secret", "replace-me")

MIN_AUTH_SECRET_LENGTH = 32
MIN_AUTH_SECRET_DISTINCT_CHARS = 12
MIN_AUTH_SECRET_ENTROPY_BITS = 96.0  # empirical estimate; a random 32-char hex secret scores ~110-125


class InsecureConfigurationError(RuntimeError):
    """Raised at start-up when production-grade configuration is unsafe. Never contains secret values."""


def estimate_entropy_bits(value: str) -> float:
    """Empirical Shannon entropy of the string x its length. A coarse heuristic to reject low-variety
    secrets such as "aaaa..." or "abcabc..."; it cannot prove randomness, so generate secrets with a CSPRNG."""
    if not value:
        return 0.0
    n = len(value)
    per_char = -sum((c / n) * math.log2(c / n) for c in Counter(value).values())
    return per_char * n


def auth_secret_problems(secret: str) -> list[str]:
    """Reasons a candidate AUTH_SECRET is unacceptable for production (empty list = acceptable)."""
    s = secret.strip()
    if not s:
        return ["AUTH_SECRET is missing"]
    lowered = s.lower()
    if s == DEV_AUTH_SECRET:
        return ["AUTH_SECRET equals the source-visible development default"]
    if lowered in KNOWN_PLACEHOLDER_SECRETS or any(m in lowered for m in PLACEHOLDER_MARKERS):
        return ["AUTH_SECRET is a known placeholder value"]
    problems = []
    if len(s) < MIN_AUTH_SECRET_LENGTH:
        problems.append(f"AUTH_SECRET is too short (minimum {MIN_AUTH_SECRET_LENGTH} characters)")
    if len(set(s)) < MIN_AUTH_SECRET_DISTINCT_CHARS or estimate_entropy_bits(s) < MIN_AUTH_SECRET_ENTROPY_BITS:
        problems.append("AUTH_SECRET has insufficient entropy")
    return problems


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BACKEND_DIR / ".env"), env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'aimo.db').as_posix()}"
    data_dir: Path = PROJECT_DIR / "data"
    auto_ingest: bool = True

    # Official Valio Aimo Junction 2026 API (see data/openapi.json)
    valio_api_base_url: str = "https://junction26.valioaimo.fi"
    valio_api_key: SecretStr = SecretStr("")
    valio_customer_id: str = "123"
    valio_timeout_seconds: float = 10.0
    valio_cache_ttl_seconds: int = 120
    valio_max_shortlist: int = 15
    # Availability amounts on STOCK lines that are treated as supplier placeholders, i.e. "quantity NOT confirmed".
    # Live Valio responses (2026-11-15) return 9999999999 for every Stock product queried; the API contract only
    # documents the Terminal placeholder 999999 (always unconfirmed, not configurable). Explicit product decision
    # for Junction 2026: 9999999999 is an unconfirmed supplier placeholder pending clarification and affected requests
    # are routed for human consideration. Final configuration — do not clear. Comma-separated numbers.
    valio_stock_quantity_sentinels: str = "9999999999"

    # Optional LLM provider for intent extraction. "none" = deterministic parser only.
    llm_provider: str = "none"  # none | anthropic | openai | gemini
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    llm_timeout_seconds: float = 20.0

    # Internal app security
    # The defaults below are for local development only; validate_runtime_security() rejects them in production.
    auth_secret: SecretStr = SecretStr(DEV_AUTH_SECRET)
    token_ttl_minutes: int = 720
    demo_password: SecretStr = SecretStr(DEV_DEMO_PASSWORD)
    agent_api_key: SecretStr = SecretStr(DEV_AGENT_API_KEY)
    seed_demo_users: bool = True
    agent_rate_limit_per_minute: int = 30
    # database = shared across worker processes (required in production); memory = single process only
    agent_rate_limit_backend: Literal["database", "memory"] = "database"
    session_cookie_secure: bool = False  # must be true in production (HTTPS); enforced at start-up
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Login brute-force protection (failures in a sliding window; stored in the shared database).
    login_rate_limit_enabled: bool = True
    login_window_seconds: int = 900
    login_max_failures_per_account_ip: int = 5  # same account from the same source
    login_max_failures_per_account: int = 20  # same account from any source (defeats IP rotation)
    login_max_failures_per_ip: int = 50  # same source against any account (defeats password spraying)
    # Number of trusted reverse proxies in front of the backend. 0 = use the socket peer address and ignore
    # X-Forwarded-For (which a client could forge). N = take the N-th address from the right of X-Forwarded-For.
    trusted_proxy_count: int = 0
    # X-Forwarded-For is honoured ONLY when the TCP peer is one of these proxy addresses. A client that reaches
    # the backend directly can therefore never spoof its address, even if TRUSTED_PROXY_COUNT > 0.
    trusted_proxy_ips: str = "127.0.0.1,::1"

    # Database connection pool (PostgreSQL; SQLite uses the same pool settings but is single-writer).
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout_seconds: int = 30
    db_pool_recycle_seconds: int = 1800  # recycle before typical proxy/firewall idle timeouts

    # Logs: "text" (human readable) or "json" (one JSON object per line, for log aggregation)
    log_format: Literal["text", "json"] = "text"

    timezone: str = "Europe/Helsinki"

    @property
    def is_production(self) -> bool:
        """Fail-closed: anything not explicitly a development/test environment is production-grade."""
        return self.app_env.strip().lower() not in NON_PRODUCTION_ENVS

    def runtime_security_problems(self) -> list[str]:
        """Production configuration problems. Messages name settings only and never contain their values."""
        if not self.is_production:
            return []
        problems = auth_secret_problems(self.auth_secret.get_secret_value())
        if not self.session_cookie_secure:
            problems.append("SESSION_COOKIE_SECURE must be true (session cookies require HTTPS)")
        if self.seed_demo_users:
            problems.append("SEED_DEMO_USERS must be false (demo accounts share a published password)")
        if self.agent_api_key.get_secret_value().strip() == DEV_AGENT_API_KEY:
            problems.append("AGENT_API_KEY equals the source-visible development default (set a new key or leave it empty)")
        if self.token_ttl_minutes <= 0:
            problems.append("TOKEN_TTL_MINUTES must be positive")
        if not self.login_rate_limit_enabled:
            problems.append("LOGIN_RATE_LIMIT_ENABLED must be true")
        if self.agent_rate_limit_backend != "database":
            problems.append("AGENT_RATE_LIMIT_BACKEND must be 'database' (the memory limiter is per worker process)")
        return problems

    def validate_runtime_security(self) -> None:
        problems = self.runtime_security_problems()
        if problems:
            raise InsecureConfigurationError(
                f"Refusing to start with APP_ENV={self.app_env!r}: " + "; ".join(problems)
                + ". Generate a secret with: python -c \"import secrets; print(secrets.token_urlsafe(48))\"")
        if not self.is_production and self.auth_secret.get_secret_value() == DEV_AUTH_SECRET:
            log.warning("AUTH_SECRET is the development default (acceptable only for APP_ENV=%s)", self.app_env)

    @property
    def live_api_configured(self) -> bool:
        return bool(self.valio_api_key.get_secret_value().strip())

    @property
    def llm_configured(self) -> bool:
        return self.llm_provider.lower() != "none" and bool(self.llm_api_key.get_secret_value().strip())

    @property
    def stock_quantity_sentinel_values(self) -> set[float]:
        out: set[float] = set()
        for part in self.valio_stock_quantity_sentinels.split(","):
            part = part.strip()
            if part:
                out.add(float(part))
        return out

    @property
    def trusted_proxy_ip_list(self) -> set[str]:
        return {o.strip() for o in self.trusted_proxy_ips.split(",") if o.strip()}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def secret_values(self) -> list[str]:
        """All secret values, used by the log/audit redaction filter."""
        vals = [
            self.valio_api_key.get_secret_value(),
            self.llm_api_key.get_secret_value(),
            self.auth_secret.get_secret_value(),
            self.demo_password.get_secret_value(),
            self.agent_api_key.get_secret_value(),
        ]
        return [v for v in vals if v and len(v) >= 4]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    # Validated here so an unsafe production configuration fails at import/start-up, before any request.
    s.validate_runtime_security()
    return s
