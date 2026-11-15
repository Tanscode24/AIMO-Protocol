"""Test setup: an isolated SQLite DB and a subset of the REAL official dataset
(specific products used by the demo scenarios + the first 300 products).
External Valio API calls are mocked with httpx.MockTransport."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
REAL_DATA = ROOT / "data"
TMP = Path(tempfile.mkdtemp(prefix="aimo-test-"))
SUBSET_IDS = {
    "464179", "464201", "464401", "464398", "463359", "470350", "464440", "469330", "464119",  # milk
    "474235", "469059", "470999", "466511", "468959", "462324", "465116",  # pasta
    "464411", "464205", "464761", "463543", "464076",  # butter
}


def _build_subset() -> Path:
    data_dir = TMP / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    products = json.loads((REAL_DATA / "valio_aimo_junction26_product_data.json").read_text(encoding="utf-8"))
    subset = products[:300] + [p for p in products[300:] if p["id"] in SUBSET_IDS]
    (data_dir / "valio_aimo_junction26_product_data.json").write_text(json.dumps(subset), encoding="utf-8")
    for f in ("valio_aimo_junction26_product_data_schema.json", "valio_aimo_junction26_delivery_reliability.csv",
              "valio_aimo_junction26_carbon_footprint.csv"):
        shutil.copy(REAL_DATA / f, data_dir / f)
    return data_dir


DATA_DIR = _build_subset()
# AIMO_TEST_DATABASE_URL runs the suite against another EMPTY, disposable database (e.g. PostgreSQL).
# Never point it at a database whose data you want to keep: tests write to it.
os.environ["DATABASE_URL"] = os.environ.get("AIMO_TEST_DATABASE_URL") or f"sqlite:///{(TMP / 'test.db').as_posix()}"
os.environ["DATA_DIR"] = str(DATA_DIR)
os.environ["VALIO_API_KEY"] = "test-valio-key-123456"
os.environ["VALIO_CACHE_TTL_SECONDS"] = "0"
os.environ["LLM_PROVIDER"] = "none"
os.environ["AGENT_API_KEY"] = "test-agent-key-123456"
os.environ["DEMO_PASSWORD"] = "test-password"
os.environ["AUTH_SECRET"] = "test-auth-secret-abcdef"
os.environ["AGENT_RATE_LIMIT_PER_MINUTE"] = "1000"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.integrations import valio_client as vc  # noqa: E402
from app.main import app  # noqa: E402


class MockValio:
    """Configurable fake of POST /api/price and POST /api/availability."""

    def __init__(self):
        self.calls: list[dict] = []
        self.price: dict[str, dict] = {}  # pid -> overrides
        self.avail: dict[str, dict] = {}
        self.default_price = 2.0
        self.mode: str | None = None  # None | timeout | http500 | 422 | 429 | malformed

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.calls.append({"path": request.url.path, "headers": dict(request.headers), "body": body})
        if self.mode == "timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        if self.mode == "http500":
            return httpx.Response(500, json={"detail": "boom"})
        if self.mode in ("422", "429"):
            return httpx.Response(int(self.mode), json={"detail": []})
        if self.mode == "malformed":
            return httpx.Response(200, json={"unexpected": True})
        if request.url.path == "/api/ping":
            return httpx.Response(200, json={"ok": True})
        ids = body["products"]
        d = body["requestedDate"]
        if request.url.path == "/api/price":
            items = []
            for p in ids:
                o = self.price.get(p, {})
                if o.get("missing"):
                    continue
                value = o.get("value", self.default_price)
                items.append({
                    "productId": p, "quantity": 1.0,
                    "catalogPrice": None if o.get("error") else {"value": value, "valueWithTax": round(value * 1.135, 4),
                                                                 "validFrom": "2026-01-01", "validTo": "2026-12-31", "currency": "EUR"},
                    "tax": {"taxRate": 13.5, "taxValue": round(value * 0.135, 4)},
                    "netPriceFound": False, "error": bool(o.get("error")),
                })
            return httpx.Response(200, json={"customerId": body["customer"], "currency": "EUR", "pricingDate": d, "products": items})
        items = []
        for p in ids:
            o = self.avail.get(p, {})
            if o.get("missing"):
                continue
            lines = [] if o.get("no_lines") else [{
                "salesBlock": bool(o.get("line_block")), "availableAmount": o.get("amount", 100000),
                "availableDate": o.get("date", d), "salesUnit": o.get("unit", "__CATALOGUE__"),
            }]
            items.append({"productId": p, "closingTime": o.get("closing", f"{d}T23:59:00"),
                          "salesBlock": bool(o.get("block")), "logisticModel": o.get("model", "Stock"), "scheduleLines": lines})
        return httpx.Response(200, json={"timeNow": "2026-11-14T10:00:00", "requestedDate": f"{d}T00:00:00", "products": items})


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def mock_valio(client):
    from app.database import SessionLocal
    from app.models import Product

    mv = MockValio()
    with SessionLocal() as db:
        units = {p.id: p.sales_unit for p in db.query(Product.id, Product.sales_unit)}

    orig = mv.handler

    def handler(request):
        resp = orig(request)
        if request.url.path == "/api/availability" and resp.status_code == 200:
            data = json.loads(resp.content)
            for item in data.get("products", []):
                for line in item["scheduleLines"]:
                    if line["salesUnit"] == "__CATALOGUE__":
                        line["salesUnit"] = units.get(item["productId"], "ST")
            return httpx.Response(200, json=data)
        return resp

    c = vc.ValioClient(get_settings(), transport=httpx.MockTransport(handler))
    vc.set_valio_client(c)
    yield mv
    vc.set_valio_client(None)


@pytest.fixture()
def db(client):
    from app.database import SessionLocal

    with SessionLocal() as s:
        yield s


def login(client, email: str) -> dict:
    r = client.post("/api/auth/login", json={"email": email, "password": "test-password"})
    assert r.status_code == 200, r.text
    client.cookies.clear()  # tests authenticate explicitly with bearer tokens; never leak a session cookie
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture()
def buyer(client):
    return login(client, "buyer@aimo.local")


@pytest.fixture()
def manager(client):
    return login(client, "manager@aimo.local")


@pytest.fixture()
def admin(client):
    return login(client, "admin@aimo.local")


@pytest.fixture()
def viewer(client):
    return login(client, "viewer@aimo.local")


@pytest.fixture()
def buyer2(client):
    return login(client, "buyer2@aimo.local")


@pytest.fixture()
def nordic_buyer(client):
    return login(client, "buyer@nordic.local")


@pytest.fixture()
def nordic_manager(client):
    return login(client, "manager@nordic.local")


AGENT = {"X-Agent-Key": "test-agent-key-123456"}


@pytest.fixture()
def fresh_policy(client, admin):
    """Reset the policy to defaults before a test that depends on it."""
    pol = client.get("/api/policies/active", headers=admin).json()
    r = client.put(f"/api/policies/{pol['id']}", headers=admin, json={
        "max_order_value": 500, "auto_approval_enabled": True, "require_human_approval_above_limit": True,
        "allowed_categories": [], "blocked_categories": [], "blocked_products": [], "allow_substitutions": True,
        "substitution_requires_approval": True, "unknown_allergen_action": "BLOCK", "unverified_price_action": "REQUIRE_APPROVAL",
        "unverified_availability_action": "REQUIRE_APPROVAL", "terminal_quantity_action": "REQUIRE_APPROVAL",
        "unverified_conversion_action": "REQUIRE_APPROVAL", "allow_late_delivery": False, "duplicate_window_minutes": 0,
        "clear_max_quantity": True, "weight_price": 0.4, "weight_reliability": 0.3, "weight_sustainability": 0.2, "weight_match": 0.1,
    })
    assert r.status_code == 200, r.text
    return r.json()
