"""Regression tests for the three security findings in docs/FINAL_QA_AUDIT.md:

  P1  production AUTH_SECRET / cookie enforcement
  P1  login brute-force throttling
  P2  server-side session revocation (logout, expiry, deactivation, password change)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.config import DEV_AGENT_API_KEY, DEV_AUTH_SECRET, InsecureConfigurationError, Settings, get_settings
from app.security import _b64, _unb64

BACKEND = Path(__file__).resolve().parents[2]
PW = "test-password"


# ====================================================================== helpers
def prod(**overrides) -> Settings:
    """A production Settings object that is valid unless a test overrides a field. Ignores backend/.env."""
    base = dict(app_env="production", auth_secret=secrets.token_urlsafe(48), session_cookie_secure=True,
                seed_demo_users=False, agent_api_key="")
    base.update(overrides)
    return Settings(_env_file=None, **base)


def sign(payload: dict) -> str:
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(get_settings().auth_secret.get_secret_value().encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(sig)}"


def claims(token: str) -> dict:
    return json.loads(_unb64(token.split(".", 1)[0]))


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def do_login(client, email: str, password: str = PW, headers: dict | None = None):
    r = client.post("/api/auth/login", json={"email": email, "password": password}, headers=headers or {})
    client.cookies.clear()  # never let a cookie leak into a later bearer-only assertion
    return r


def token_for(client, email: str) -> str:
    r = do_login(client, email)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def body_without_cid(r) -> dict:
    return {k: v for k, v in r.json().items() if k != "correlation_id"}


@pytest.fixture(autouse=True)
def clean_throttle(client):
    from app.database import SessionLocal
    from app.services.login_throttle import reset_all

    with SessionLocal() as s:
        reset_all(s)
    yield
    with SessionLocal() as s:
        reset_all(s)


@pytest.fixture()
def tune(monkeypatch):
    """Temporarily change live settings (get_settings() is cached, so patch the cached instance)."""
    s = get_settings()

    def _set(**kw):
        for k, v in kw.items():
            monkeypatch.setattr(s, k, v)
    return _set


# ====================================================================== FIX 1 — production secret enforcement
class TestProductionSecretEnforcement:
    def test_missing_secret_fails(self):
        with pytest.raises(InsecureConfigurationError, match="AUTH_SECRET is missing"):
            prod(auth_secret="").validate_runtime_security()

    def test_development_default_fails(self):
        with pytest.raises(InsecureConfigurationError, match="development default"):
            prod(auth_secret=DEV_AUTH_SECRET).validate_runtime_security()

    def test_omitted_secret_falls_back_to_default_and_fails(self):
        s = Settings(_env_file=None, app_env="production", session_cookie_secure=True, seed_demo_users=False,
                     agent_api_key="")
        with pytest.raises(InsecureConfigurationError):
            s.validate_runtime_security()

    @pytest.mark.parametrize("value", ["change-me-to-a-long-random-string", "CHANGE-ME-please-0123456789abcdefghij",
                                       "my-placeholder-secret-value-0123456789abcdef"])
    def test_known_placeholders_fail(self, value):
        with pytest.raises(InsecureConfigurationError, match="placeholder"):
            prod(auth_secret=value).validate_runtime_security()

    @pytest.mark.parametrize("value,reason", [
        ("Sh0rt-but-random-Xy9", "too short"),
        ("a" * 64, "insufficient entropy"),
        ("abcabcabc" * 8, "insufficient entropy"),
        ("0123456789" * 4, "insufficient entropy"),
    ])
    def test_weak_secrets_fail(self, value, reason):
        with pytest.raises(InsecureConfigurationError, match=reason):
            prod(auth_secret=value).validate_runtime_security()

    @pytest.mark.parametrize("value", [secrets.token_urlsafe(48), secrets.token_hex(32), secrets.token_hex(16),
                                       secrets.token_urlsafe(32)])
    def test_strong_generated_secret_passes(self, value):
        prod(auth_secret=value).validate_runtime_security()  # no exception

    def test_error_never_contains_secret_value(self):
        weak = "visible-weak-secret-XYZ"
        with pytest.raises(InsecureConfigurationError) as e:
            prod(auth_secret=weak).validate_runtime_security()
        assert weak not in str(e.value)

    @pytest.mark.parametrize("env", ["staging", "prod", "Production", "prodution", "qa"])
    def test_unknown_or_production_like_envs_are_enforced(self, env):
        assert prod(app_env=env, auth_secret=DEV_AUTH_SECRET).runtime_security_problems()

    @pytest.mark.parametrize("env", ["local", "development", "dev", "demo", "test", "testing", "ci"])
    def test_development_and_test_modes_keep_working(self, env):
        s = Settings(_env_file=None, app_env=env)  # all development defaults
        assert not s.is_production
        s.validate_runtime_security()

    def test_production_requires_secure_cookie(self):
        with pytest.raises(InsecureConfigurationError, match="SESSION_COOKIE_SECURE"):
            prod(session_cookie_secure=False).validate_runtime_security()

    def test_production_rejects_demo_users_and_default_agent_key(self):
        problems = " ".join(prod(seed_demo_users=True, agent_api_key=DEV_AGENT_API_KEY).runtime_security_problems())
        assert "SEED_DEMO_USERS" in problems and "AGENT_API_KEY" in problems

    def test_production_rejects_disabled_login_throttle(self):
        assert any("LOGIN_RATE_LIMIT" in p for p in prod(login_rate_limit_enabled=False).runtime_security_problems())

    # ---- real process start-up (before any request is served)
    def _start(self, tmp_path, **env) -> subprocess.CompletedProcess:
        e = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path / 'startup.db'}", "AUTO_INGEST": "false",
             "VALIO_API_KEY": "", "PYTHONPATH": str(BACKEND), **env}
        code = ("from fastapi.testclient import TestClient\nfrom app.main import app\n"
                "with TestClient(app) as c: print('HEALTH', c.get('/health').status_code)")
        return subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=e, capture_output=True, text=True,
                              timeout=120)

    def test_process_refuses_to_start_in_production_with_missing_secret(self, tmp_path):
        r = self._start(tmp_path, APP_ENV="production", AUTH_SECRET="", SESSION_COOKIE_SECURE="true",
                        SEED_DEMO_USERS="false", AGENT_API_KEY="")
        assert r.returncode != 0 and "HEALTH" not in r.stdout
        assert "Refusing to start" in r.stderr and "AUTH_SECRET is missing" in r.stderr

    def test_process_refuses_weak_secret_without_leaking_it(self, tmp_path):
        weak = "leakcheck-weak-value-42"
        r = self._start(tmp_path, APP_ENV="production", AUTH_SECRET=weak, SESSION_COOKIE_SECURE="true",
                        SEED_DEMO_USERS="false", AGENT_API_KEY="")
        assert r.returncode != 0 and "Refusing to start" in r.stderr
        assert weak not in r.stderr and weak not in r.stdout

    def test_process_starts_in_production_with_strong_secret(self, tmp_path):
        r = self._start(tmp_path, APP_ENV="production", AUTH_SECRET=secrets.token_urlsafe(48),
                        SESSION_COOKIE_SECURE="true", SEED_DEMO_USERS="false", AGENT_API_KEY="")
        assert r.returncode == 0, r.stderr[-2000:]
        assert "HEALTH 200" in r.stdout

    def test_process_starts_in_development_with_defaults(self, tmp_path):
        r = self._start(tmp_path, APP_ENV="local", AUTH_SECRET=DEV_AUTH_SECRET)
        assert r.returncode == 0, r.stderr[-2000:]
        assert "HEALTH 200" in r.stdout


# ---- cookie flags
def test_session_cookie_flags_default(client):
    r = client.post("/api/auth/login", json={"email": "buyer@aimo.local", "password": PW})
    client.cookies.clear()
    cookie = r.headers["set-cookie"].lower()
    assert "aimo_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie


def test_session_cookie_is_secure_when_configured_for_production(client, tune):
    tune(session_cookie_secure=True)
    r = client.post("/api/auth/login", json={"email": "buyer@aimo.local", "password": PW})
    client.cookies.clear()
    cookie = r.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    out = client.post("/api/auth/logout", headers=bearer(r.json()["token"]))
    gone = out.headers["set-cookie"].lower()
    assert "max-age=0" in gone and "secure" in gone and "httponly" in gone


# ====================================================================== FIX 2 — login throttling
def test_repeated_failures_trigger_429_with_retry_after(client):
    for _ in range(5):
        assert do_login(client, "buyer@aimo.local", "wrong-password").status_code == 401
    r = do_login(client, "buyer@aimo.local", "wrong-password")
    assert r.status_code == 429 and r.json()["code"] == "RATE_LIMITED"
    assert int(r.headers["Retry-After"]) > 0
    # While throttled even the correct password is refused (no guessing oracle during the lock-out).
    assert do_login(client, "buyer@aimo.local").status_code == 429


def test_valid_login_works_before_the_limit(client):
    for _ in range(4):
        assert do_login(client, "buyer@aimo.local", "wrong-password").status_code == 401
    assert do_login(client, "buyer@aimo.local").status_code == 200


def test_success_resets_account_failures(client):
    for _ in range(4):
        do_login(client, "buyer@aimo.local", "wrong-password")
    assert do_login(client, "buyer@aimo.local").status_code == 200
    for _ in range(5):  # a full fresh allowance after the reset
        assert do_login(client, "buyer@aimo.local", "wrong-password").status_code == 401
    assert do_login(client, "buyer@aimo.local", "wrong-password").status_code == 429


def test_accounts_are_throttled_independently(client):
    for _ in range(6):
        do_login(client, "buyer2@aimo.local", "wrong-password")
    assert do_login(client, "buyer2@aimo.local").status_code == 429
    assert do_login(client, "buyer@aimo.local").status_code == 200
    assert do_login(client, "Buyer2@AIMO.local ").status_code == 429  # normalised identifier, same account


def test_no_account_enumeration(client):
    wrong = do_login(client, "buyer@aimo.local", "wrong-password")
    unknown = do_login(client, "nobody-here@aimo.local", "wrong-password")
    assert wrong.status_code == unknown.status_code == 401
    assert body_without_cid(wrong) == body_without_cid(unknown)
    for _ in range(5):
        do_login(client, "buyer@aimo.local", "wrong-password")
        do_login(client, "nobody-here@aimo.local", "wrong-password")
    a, b = do_login(client, "buyer@aimo.local", "x"), do_login(client, "nobody-here@aimo.local", "x")
    assert a.status_code == b.status_code == 429
    assert body_without_cid(a) == body_without_cid(b)


def test_inactive_account_is_indistinguishable(client, admin):
    email = f"inactive-{secrets.token_hex(4)}@aimo.local"
    uid = client.post("/api/admin/users", headers=admin, json={"email": email, "name": "I", "role": "BUYER",
                                                                 "password": "inactive-pass-123"}).json()["id"]
    client.patch(f"/api/admin/users/{uid}", headers=admin, json={"active": False})
    inactive = do_login(client, email, "inactive-pass-123")
    unknown = do_login(client, "nobody-else@aimo.local", "inactive-pass-123")
    assert inactive.status_code == unknown.status_code == 401
    assert body_without_cid(inactive) == body_without_cid(unknown)


def test_protection_window_expires(client, tune):
    tune(login_window_seconds=2)
    for _ in range(6):
        do_login(client, "buyer@aimo.local", "wrong-password")
    assert do_login(client, "buyer@aimo.local").status_code == 429
    time.sleep(2.5)
    assert do_login(client, "buyer@aimo.local").status_code == 200


def test_forged_forwarded_for_is_ignored_without_trusted_proxy(client):
    for i in range(5):
        do_login(client, "buyer@aimo.local", "wrong-password", headers={"X-Forwarded-For": f"10.0.0.{i}"})
    assert do_login(client, "buyer@aimo.local", "x", headers={"X-Forwarded-For": "10.9.9.9"}).status_code == 429


def test_rotating_ips_cannot_bypass_account_limit(client, tune):
    tune(trusted_proxy_count=1, trusted_proxy_ips="testclient")  # TestClient's peer plays the trusted proxy
    for i in range(20):  # 20 different sources, each below its own per-source limit
        r = do_login(client, "buyer@aimo.local", "wrong-password", headers={"X-Forwarded-For": f"10.1.0.{i}"})
        assert r.status_code == 401
    r = do_login(client, "buyer@aimo.local", "wrong-password", headers={"X-Forwarded-For": "10.1.1.1"})
    assert r.status_code == 429
    # an unrelated account from a fresh source is unaffected
    assert do_login(client, "buyer2@aimo.local", headers={"X-Forwarded-For": "10.1.1.2"}).status_code == 200


def test_untrusted_peer_cannot_spoof_forwarded_for_even_with_proxy_mode(client, tune):
    """TRUSTED_PROXY_COUNT=1, but the TCP peer is not a configured proxy: X-Forwarded-For must be ignored, so a
    client that reaches the backend directly cannot rotate its apparent IP."""
    tune(trusted_proxy_count=1, trusted_proxy_ips="10.0.0.1")
    for i in range(5):
        do_login(client, "buyer@aimo.local", "wrong-password", headers={"X-Forwarded-For": f"198.51.100.{i}"})
    r = do_login(client, "buyer@aimo.local", "wrong-password", headers={"X-Forwarded-For": "198.51.100.99"})
    assert r.status_code == 429


def test_client_source_rules():
    from types import SimpleNamespace

    from app.services.login_throttle import client_source

    def req(peer, xff=None):
        return SimpleNamespace(client=SimpleNamespace(host=peer), headers={"x-forwarded-for": xff} if xff else {})

    trusted = {"127.0.0.1"}
    assert client_source(req("203.0.113.5", "1.1.1.1"), 0, trusted) == "203.0.113.5"  # proxy mode off
    assert client_source(req("203.0.113.5", "1.1.1.1"), 1, trusted) == "203.0.113.5"  # untrusted peer
    assert client_source(req("127.0.0.1", "1.1.1.1"), 1, trusted) == "1.1.1.1"  # trusted proxy
    # nginx appends the real peer: a forged left-most entry is ignored
    assert client_source(req("127.0.0.1", "6.6.6.6, 203.0.113.9"), 1, trusted) == "203.0.113.9"
    assert client_source(req("127.0.0.1"), 1, trusted) == "127.0.0.1"  # header missing


def test_health_endpoints(client):
    assert client.get("/health/live").json() == {"status": "ok"}
    h = client.get("/health")
    assert h.status_code == 200 and h.json()["database"] == "ok"


def test_health_returns_503_when_database_unreachable(client, monkeypatch):
    import app.database as database

    monkeypatch.setattr(database, "database_reachable", lambda: False)
    r = client.get("/health")
    assert r.status_code == 503 and r.json()["database"] == "unreachable"
    assert client.get("/health/live").status_code == 200


def test_password_spraying_from_one_source_is_limited(client, tune):
    tune(login_max_failures_per_ip=6)
    for i in range(6):
        assert do_login(client, f"spray{i}@aimo.local", "Winter2026!").status_code == 401
    assert do_login(client, "spray-next@aimo.local", "Winter2026!").status_code == 429


def test_concurrent_reservations_cannot_exceed_limit(client):
    """25 parallel attempts (more than the 15-connection request pool) on the same account from separate DB
    sessions: at most the limit get through. Concurrent rejections may briefly over-count (safe side)."""
    from app.database import SessionLocal
    from app.services import login_throttle

    n = 25
    s, results, barrier = get_settings(), [], threading.Barrier(n)

    def attempt():
        barrier.wait()
        with SessionLocal() as db:
            results.append(login_throttle.reserve(db, s, "race@aimo.local", "198.51.100.7").throttled)

    threads = [threading.Thread(target=attempt) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == n
    assert results.count(False) <= s.login_max_failures_per_account_ip


def test_concurrent_http_login_failures_are_limited(client):
    def attempt(_):
        return client.post("/api/auth/login", json={"email": "buyer@aimo.local", "password": "wrong"}).status_code

    with ThreadPoolExecutor(max_workers=12) as pool:
        codes = list(pool.map(attempt, range(24)))
    client.cookies.clear()
    assert codes.count(401) <= 5 and codes.count(429) >= 19, codes


def test_window_expiry_in_limiter_service(client):
    from app.database import SessionLocal
    from app.services import login_throttle

    s, t0 = get_settings(), time.time()
    with SessionLocal() as db:
        for _ in range(5):
            assert not login_throttle.reserve(db, s, "svc@aimo.local", "203.0.113.1", now=t0).throttled
        assert login_throttle.reserve(db, s, "svc@aimo.local", "203.0.113.1", now=t0 + 1).throttled
        assert not login_throttle.reserve(db, s, "svc@aimo.local", "203.0.113.1",
                                          now=t0 + s.login_window_seconds + 1).throttled


def test_identifiers_are_not_stored_in_plaintext(client):
    from app.database import SessionLocal
    from app.models import LoginAttempt

    do_login(client, "buyer@aimo.local", "wrong-password")
    with SessionLocal() as db:
        rows = db.query(LoginAttempt).all()
    assert rows and all("@" not in r.account_key and "testclient" not in r.source_key for r in rows)


# ====================================================================== FIX 3 — session revocation
def test_login_creates_active_server_side_session(client):
    from app.database import SessionLocal
    from app.models import AuthSession, User

    c = claims(token_for(client, "buyer@aimo.local"))
    assert c["sid"].startswith("ses_")
    with SessionLocal() as db:
        row = db.get(AuthSession, c["sid"])
        assert row is not None and row.revoked_at is None
        assert row.user_id == c["sub"] == db.query(User).filter_by(email="buyer@aimo.local").one().id


def test_retained_bearer_token_fails_after_logout(client):
    tok = token_for(client, "buyer@aimo.local")
    assert client.get("/api/auth/me", headers=bearer(tok)).status_code == 200
    assert client.get("/api/products", headers=bearer(tok)).status_code == 200
    assert client.post("/api/auth/logout", headers=bearer(tok)).json() == {"ok": True}
    for path in ("/api/auth/me", "/api/products", "/api/procurement/requests"):
        assert client.get(path, headers=bearer(tok)).status_code == 401


def test_revoked_cookie_session_fails_and_its_bearer_copy_too(client):
    r = client.post("/api/auth/login", json={"email": "buyer@aimo.local", "password": PW})
    cookie, tok = r.cookies.get("aimo_session"), r.json()["token"]
    client.cookies.clear()
    jar = {"aimo_session": cookie}
    assert client.get("/api/auth/me", cookies=jar).status_code == 200
    out = client.post("/api/auth/logout", cookies=jar, headers={"X-Requested-With": "aimo-web"})
    client.cookies.clear()
    assert out.status_code == 200
    assert client.get("/api/auth/me", cookies=jar).status_code == 401
    assert client.get("/api/auth/me", headers=bearer(tok)).status_code == 401  # same session, any transport


def test_logout_is_audited(client, admin):
    tok = token_for(client, "buyer@aimo.local")
    client.post("/api/auth/logout", headers=bearer(tok))
    items = client.get("/api/audit", params={"action": "USER_LOGOUT", "page_size": 50}, headers=admin).json()["items"]
    assert any(claims(tok)["sid"] in json.dumps(e) for e in items)


def test_logout_of_one_session_keeps_other_sessions(client):
    a, b = token_for(client, "buyer@aimo.local"), token_for(client, "buyer@aimo.local")
    assert claims(a)["sid"] != claims(b)["sid"]
    client.post("/api/auth/logout", headers=bearer(a))
    assert client.get("/api/auth/me", headers=bearer(a)).status_code == 401
    assert client.get("/api/auth/me", headers=bearer(b)).status_code == 200


def test_logout_with_garbage_or_missing_token_is_harmless(client):
    assert client.post("/api/auth/logout").json() == {"ok": True}
    assert client.post("/api/auth/logout", headers=bearer("not.a-token")).json() == {"ok": True}


def test_server_side_expired_session_fails(client):
    from datetime import datetime, timedelta, timezone

    from app.database import SessionLocal
    from app.models import AuthSession

    tok = token_for(client, "buyer@aimo.local")
    with SessionLocal() as db:
        db.get(AuthSession, claims(tok)["sid"]).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/auth/me", headers=bearer(tok)).status_code == 401


def test_expired_token_fails(client, tune):
    tok = token_for(client, "buyer@aimo.local")
    c = claims(tok)
    expired = sign({**c, "exp": int(time.time()) - 5})
    assert client.get("/api/auth/me", headers=bearer(expired)).status_code == 401


def test_deactivated_user_loses_access_and_reactivation_does_not_resurrect(client, admin):
    email = f"deact-{secrets.token_hex(4)}@aimo.local"
    uid = client.post("/api/admin/users", headers=admin, json={"email": email, "name": "D", "role": "BUYER",
                                                                 "password": "deact-pass-1234"}).json()["id"]
    tok = do_login(client, email, "deact-pass-1234").json()["token"]
    assert client.get("/api/auth/me", headers=bearer(tok)).status_code == 200
    assert client.patch(f"/api/admin/users/{uid}", headers=admin, json={"active": False}).status_code == 200
    assert client.get("/api/auth/me", headers=bearer(tok)).status_code == 401
    assert do_login(client, email, "deact-pass-1234").status_code == 401
    client.patch(f"/api/admin/users/{uid}", headers=admin, json={"active": True})
    assert client.get("/api/auth/me", headers=bearer(tok)).status_code == 401  # old session stays revoked
    assert do_login(client, email, "deact-pass-1234").status_code == 200


def test_password_change_invalidates_existing_sessions(client, admin):
    from app.database import SessionLocal
    from app.models import User
    from app.security import hash_password

    email = f"pwchange-{secrets.token_hex(4)}@aimo.local"
    client.post("/api/admin/users", headers=admin, json={"email": email, "name": "P", "role": "BUYER",
                                                          "password": "old-password-123"})
    tok = do_login(client, email, "old-password-123").json()["token"]
    assert client.get("/api/auth/me", headers=bearer(tok)).status_code == 200
    with SessionLocal() as db:
        db.query(User).filter_by(email=email).one().password_hash = hash_password("new-password-456")
        db.commit()
    assert client.get("/api/auth/me", headers=bearer(tok)).status_code == 401
    assert do_login(client, email, "new-password-456").status_code == 200


def test_legacy_token_without_session_id_is_rejected(client):
    tok = token_for(client, "buyer@aimo.local")
    c = claims(tok)
    legacy = sign({"sub": c["sub"], "role": c["role"], "exp": c["exp"]})  # pre-remediation token shape
    assert client.get("/api/auth/me", headers=bearer(legacy)).status_code == 401


def test_session_cannot_be_reused_for_another_user(client):
    buyer_tok, viewer_tok = token_for(client, "buyer@aimo.local"), token_for(client, "viewer@aimo.local")
    swapped = sign({**claims(buyer_tok), "sub": claims(viewer_tok)["sub"]})
    assert client.get("/api/auth/me", headers=bearer(swapped)).status_code == 401


def test_role_claim_in_token_is_not_trusted(client):
    c = claims(token_for(client, "buyer@aimo.local"))
    elevated = sign({**c, "role": "ADMIN"})
    me = client.get("/api/auth/me", headers=bearer(elevated)).json()
    assert me["role"] == "BUYER" and "approve" not in me["permissions"]
    assert client.get("/api/admin/users", headers=bearer(elevated)).status_code == 403


def test_agent_keys_are_unaffected_by_user_sessions(client):
    from app.tests.conftest import AGENT

    assert client.get("/api/agent/v1/me", headers=AGENT).status_code == 200


def test_unexpected_error_returns_500_with_correlation_id_and_no_internals(client):
    """A failing dependency (e.g. database outage) must yield a clean JSON 500 carrying the correlation id."""
    from app.main import app

    def boom():
        raise RuntimeError("psycopg OperationalError: connection to server at 10.0.0.5 port 5432 failed")

    if not any(getattr(r, "path", None) == "/__test_boom" for r in app.router.routes):
        app.add_api_route("/__test_boom", boom, include_in_schema=False)
    r = client.get("/__test_boom", headers={"X-Correlation-ID": "cor-test-500"})
    assert r.status_code == 500
    body = r.json()
    assert body["code"] == "INTERNAL_ERROR" and body["correlation_id"] == "cor-test-500"
    assert r.headers["X-Correlation-ID"] == "cor-test-500"
    assert "10.0.0.5" not in r.text and "psycopg" not in r.text
    assert client.get("/health/live").status_code == 200  # the server keeps serving
