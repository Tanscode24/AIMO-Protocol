"""Client for the official Valio Aimo Junction 2026 API (data/openapi.json).

Implemented operations (exact paths from the OpenAPI document):
  GET  /api/ping
  POST /api/price          body: ProductsRequest {customer, requestedDate, products[1..1000]}
  POST /api/availability   body: ProductsRequest

The API key is sent only in the X-API-Key header from the backend. It is never logged,
returned or forwarded to the browser. There is no order endpoint in the API.

Undisclosed rate limits apply, so callers query only shortlisted products, results are
cached per (operation, product, date) for VALIO_CACHE_TTL_SECONDS, and 429 gets one retry.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.config import Settings, get_settings

log = logging.getLogger("app.valio")

MAX_PRODUCTS_PER_CALL = 1000
PRICE_PATH = "/api/price"
AVAILABILITY_PATH = "/api/availability"
PING_PATH = "/api/ping"


# ------------------------------------------------------------- response models (OpenAPI components)
class _M(BaseModel):
    model_config = ConfigDict(extra="allow")


class Price(_M):
    value: float
    valueWithTax: float
    validFrom: str
    validTo: str
    currency: str


class PriceWithTax(_M):
    value: float
    validFrom: str
    validTo: str
    currency: str


class Tax(_M):
    taxRate: float
    taxValue: float


class ProductPrice(_M):
    productId: str
    quantity: float
    catalogPrice: Price | None = None
    priceWithTax: PriceWithTax | None = None
    comparisonPrice: Price | None = None
    comparisonUnit: str | None = None
    tax: Tax | None = None
    listPrice: Price | None = None
    netPriceFound: bool
    error: bool


class PriceResponse(_M):
    customerId: str
    currency: str
    pricingDate: str
    products: list[ProductPrice]


class ScheduleLine(_M):
    salesBlock: bool
    availableAmount: float
    availableDate: str | None = None
    salesUnit: str


class ProductAvailability(_M):
    productId: str
    closingTime: str | None = None
    salesBlock: bool
    logisticModel: str | None = None
    scheduleLines: list[ScheduleLine]


class AvailabilityResponse(_M):
    timeNow: str
    requestedDate: str
    products: list[ProductAvailability]


# ------------------------------------------------------------- call results
@dataclass
class CallResult:
    """Outcome of one logical call (possibly several HTTP chunks)."""

    operation: str
    status: str  # OK | API_ERROR | TIMEOUT
    items: dict[str, Any] = field(default_factory=dict)  # productId -> ProductPrice / ProductAvailability
    meta: dict[str, Any] = field(default_factory=dict)  # currency, pricingDate, timeNow ...
    error: str | None = None
    live: bool = True
    simulated: bool = False
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    http_status: int | None = None
    from_cache: int = 0

    @property
    def source(self) -> str:
        return f"Valio API POST {PRICE_PATH if self.operation == 'price' else AVAILABILITY_PATH}"


class ValioClient:
    def __init__(self, settings: Settings | None = None, transport: httpx.BaseTransport | None = None, on_call=None):
        self.settings = settings or get_settings()
        self._transport = transport
        if on_call is None:
            from app.services.integration_status import record_call

            on_call = record_call
        self.on_call = on_call
        self._cache: dict[tuple[str, str, str], tuple[float, Any, dict]] = {}
        self._lock = threading.Lock()

    # -- helpers
    @property
    def configured(self) -> bool:
        return self.settings.live_api_configured

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.settings.valio_api_base_url.rstrip("/"),
            headers={"X-API-Key": self.settings.valio_api_key.get_secret_value(), "Accept": "application/json"},
            timeout=httpx.Timeout(self.settings.valio_timeout_seconds),
            transport=self._transport,
        )

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    def _cache_get(self, op: str, pid: str, day: str):
        with self._lock:
            hit = self._cache.get((op, pid, day))
            if hit and time.time() - hit[0] < self.settings.valio_cache_ttl_seconds:
                return hit[1], hit[2]
        return None

    def _cache_put(self, op: str, pid: str, day: str, item: Any, meta: dict) -> None:
        with self._lock:
            self._cache[(op, pid, day)] = (time.time(), item, meta)

    # -- public operations
    def ping(self) -> dict:
        if not self.configured:
            self.on_call("ping", "NOT_CONFIGURED")
            return {"status": "NOT_CONFIGURED", "detail": "VALIO_API_KEY is not set in backend/.env", "latency_ms": None}
        t0 = time.time()
        try:
            with self._client() as c:
                r = c.get(PING_PATH)
            latency = int((time.time() - t0) * 1000)
            if r.status_code == 200:
                out = {"status": "OK", "detail": "Valio API reachable and key accepted", "latency_ms": latency}
            elif r.status_code in (401, 403):
                out = {"status": "AUTH_ERROR", "detail": f"API key rejected (HTTP {r.status_code})", "latency_ms": latency}
            elif r.status_code == 429:
                out = {"status": "RATE_LIMITED", "detail": "HTTP 429 rate limit", "latency_ms": latency}
            else:
                out = {"status": "API_ERROR", "detail": f"HTTP {r.status_code}", "latency_ms": latency}
            self.on_call("ping", out["status"], http_status=r.status_code, latency_ms=latency,
                         error=None if out["status"] == "OK" else out["detail"])
            return out
        except httpx.TimeoutException:
            self.on_call("ping", "TIMEOUT", error="Ping timed out")
            return {"status": "TIMEOUT", "detail": "Ping timed out", "latency_ms": None}
        except httpx.HTTPError as e:
            self.on_call("ping", "API_ERROR", error=f"Connection error: {type(e).__name__}")
            return {"status": "API_ERROR", "detail": f"Connection error: {type(e).__name__}", "latency_ms": None}

    def get_prices(self, product_ids: list[str], requested_date: date, simulate: str | None = None) -> CallResult:
        return self._query("price", PRICE_PATH, product_ids, requested_date, simulate)

    def get_availability(self, product_ids: list[str], requested_date: date, simulate: str | None = None) -> CallResult:
        return self._query("availability", AVAILABILITY_PATH, product_ids, requested_date, simulate)

    # -- implementation
    def _query(self, op: str, path: str, product_ids: list[str], requested_date: date, simulate: str | None) -> CallResult:
        t0 = time.time()
        res = self._query_inner(op, path, product_ids, requested_date, simulate)
        if res.simulated:
            self.on_call(op, res.status, error=res.error, simulated=True, products=len(product_ids))
        elif not res.live:
            self.on_call(op, "NOT_CONFIGURED", error=res.error)
        elif res.from_cache and res.from_cache == len(set(product_ids)):
            pass  # served entirely from cache: no network call happened
        else:
            kind = res.status
            if res.http_status in (401, 403):
                kind = "AUTH_ERROR"
            elif res.http_status == 429:
                kind = "RATE_LIMITED"
            self.on_call(op, kind, http_status=res.http_status, latency_ms=int((time.time() - t0) * 1000),
                         products=len(product_ids), error=res.error)
        return res

    def _query_inner(self, op: str, path: str, product_ids: list[str], requested_date: date, simulate: str | None) -> CallResult:
        ids = list(dict.fromkeys(str(p) for p in product_ids))  # dedupe, keep order, ids stay strings
        day = requested_date.isoformat()
        if simulate == "timeout":
            return CallResult(op, "TIMEOUT", error="Simulated timeout (demo scenario) — live data could not be verified",
                              simulated=True, live=self.configured)
        if simulate == "api_error":
            return CallResult(op, "API_ERROR", error="Simulated API failure (demo scenario)", simulated=True, live=self.configured)
        if not self.configured:
            return CallResult(op, "API_ERROR", error="LIVE API UNAVAILABLE — VALIO_API_KEY not configured (demo mode)", live=False)
        if not ids:
            return CallResult(op, "OK")

        result = CallResult(op, "OK")
        todo: list[str] = []
        for pid in ids:
            hit = self._cache_get(op, pid, day)
            if hit:
                result.items[pid], meta = hit
                result.meta.update(meta)
                result.from_cache += 1
            else:
                todo.append(pid)

        model = PriceResponse if op == "price" else AvailabilityResponse
        for i in range(0, len(todo), MAX_PRODUCTS_PER_CALL):
            chunk = todo[i : i + MAX_PRODUCTS_PER_CALL]
            body = {"customer": self.settings.valio_customer_id, "requestedDate": day, "products": chunk}
            try:
                resp = self._post_with_retry(path, body)
                result.http_status = resp.status_code
                if resp.status_code == 422:
                    return CallResult(op, "API_ERROR", error="Valio API rejected the request (HTTP 422 validation error)", http_status=422)
                if resp.status_code == 429:
                    return CallResult(op, "API_ERROR", error="Valio API rate limit reached (HTTP 429)", http_status=429)
                if resp.status_code in (401, 403):
                    return CallResult(op, "API_ERROR", error=f"Valio API key rejected (HTTP {resp.status_code})", http_status=resp.status_code)
                if resp.status_code >= 400:
                    return CallResult(op, "API_ERROR", error=f"Valio API error (HTTP {resp.status_code})", http_status=resp.status_code)
                parsed = model.model_validate(resp.json())
            except httpx.TimeoutException:
                return CallResult(op, "TIMEOUT", error=f"Valio API {op} request timed out after {self.settings.valio_timeout_seconds:g}s",
                                  meta={"partial_items": len(result.items)})
            except (ValidationError, ValueError) as e:
                log.warning("Malformed Valio %s response: %s", op, type(e).__name__)
                return CallResult(op, "API_ERROR", error="Valio API returned a response that does not match the OpenAPI schema")
            except httpx.HTTPError as e:
                return CallResult(op, "API_ERROR", error=f"Could not reach Valio API ({type(e).__name__})")

            if op == "price":
                meta = {"currency": parsed.currency, "pricingDate": parsed.pricingDate}
            else:
                meta = {"timeNow": parsed.timeNow, "requestedDate": parsed.requestedDate}
            result.meta.update(meta)
            for item in parsed.products:
                pid = str(item.productId)
                result.items[pid] = item
                self._cache_put(op, pid, day, item, meta)
        return result

    def _post_with_retry(self, path: str, body: dict) -> httpx.Response:
        with self._client() as c:
            resp = c.post(path, json=body)
            if resp.status_code == 429:
                time.sleep(1.0)
                resp = c.post(path, json=body)
            return resp


_client: ValioClient | None = None


def get_valio_client() -> ValioClient:
    global _client
    if _client is None:
        _client = ValioClient()
    return _client


def set_valio_client(client: ValioClient | None) -> None:
    """Used by tests to inject a client with a mock transport."""
    global _client
    _client = client
