"""AIMO PROTOCOL — FastAPI entrypoint.

Run (from backend/):  uvicorn app.main:app --reload --port 8000
API docs:             http://localhost:8000/docs
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import admin, agent_gateway
from app.api.routes import router
from app.context import correlation_id_var, get_correlation_id, new_correlation_id
from app.config import get_settings
from app.database import SessionLocal, dispose_all, init_db, startup_lock
from app.security import install_log_redaction
from app.seed import seed
from app.services import audit
from app.services.catalogue import catalogue_count, ingest

class JsonLogFormatter(logging.Formatter):
    """One JSON object per line; secrets are already removed by the redaction filter on the handler."""

    def format(self, record: logging.LogRecord) -> str:
        import json

        doc = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"), "level": record.levelname, "logger": record.name,
               "msg": record.getMessage(), "correlation_id": get_correlation_id()}
        if record.exc_info:
            doc["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(doc, ensure_ascii=False)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
if get_settings().log_format == "json":
    for _h in logging.getLogger().handlers:
        _h.setFormatter(JsonLogFormatter())
install_log_redaction()
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    s = get_settings()
    s.validate_runtime_security()  # fail closed before serving any request (also enforced in get_settings)
    with startup_lock():  # one worker bootstraps at a time; the others then find nothing to do
        init_db()
        with SessionLocal() as db:
            seed(db)
            if catalogue_count(db) == 0 and s.auto_ingest:
                log.info("Catalogue empty — ingesting official dataset from %s (one-time, ~30 s)", s.data_dir)
                try:
                    stats = ingest(db)
                    audit.record(db, "CATALOGUE_INGESTED", reason="Automatic ingestion at startup", payload=stats, commit=True)
                except Exception as e:  # keep the API up; Trust Center will show 0 products
                    log.error("Catalogue ingestion failed: %s", e)
            log.info("AIMO PROTOCOL ready — %s products, mode=%s", catalogue_count(db),
                     "LIVE" if s.live_api_configured else "DEMO (VALIO_API_KEY not set)")
    yield
    # Graceful shutdown: uvicorn drains in-flight requests first; then close pooled database connections.
    dispose_all()
    log.info("AIMO PROTOCOL shut down cleanly")


app = FastAPI(
    title="AIMO PROTOCOL API",
    version=__version__,
    description=(
        "The Trusted Operating Layer for Autonomous Commerce. AI recommends. Policy controls. "
        "Humans authorize when required. The system records everything.\n\n"
        "These are AIMO's internal endpoints. Valio Aimo endpoints (POST /api/price, POST /api/availability) are "
        "called only server-side. No supplier orders are ever placed: the Valio API has no order endpoint."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,  # HttpOnly session cookie for the web UI (explicit origin allow-list, never "*")
    allow_methods=["GET", "POST", "PUT", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Agent-Key", "X-Requested-With", "X-Correlation-ID"],
    expose_headers=["X-Correlation-ID", "Retry-After"],
)


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    cid = new_correlation_id(request.headers.get("x-correlation-id"))
    token = correlation_id_var.set(cid)
    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001
        # Handle unexpected errors HERE (inside the correlation context) instead of letting them reach Starlette's
        # outermost ServerErrorMiddleware, which re-raises after responding: that dropped the keep-alive
        # connection (the client's next request failed with no response) and lost the correlation id.
        # Observed during a PostgreSQL outage test; see docs/FINAL_QA_AUDIT.md §18.
        log.error("Unhandled error: %s", type(exc).__name__, exc_info=exc)
        response = _err(500, "Internal error. The incident was logged without sensitive data.")
    finally:
        correlation_id_var.reset(token)
    response.headers["X-Correlation-ID"] = cid
    response.headers["Cache-Control"] = "no-store"
    return response


CODES = {400: "BAD_REQUEST", 401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND", 409: "CONFLICT",
         422: "VALIDATION_ERROR", 429: "RATE_LIMITED", 500: "INTERNAL_ERROR"}


def _err(status: int, message: str, details=None, headers=None) -> JSONResponse:
    body = {"error": message, "code": CODES.get(status, f"HTTP_{status}"), "correlation_id": get_correlation_id()}
    if details is not None:
        body["details"] = details
    return JSONResponse(status_code=status, content=body, headers=headers)


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    return _err(exc.status_code, exc.detail if isinstance(exc.detail, str) else "Request failed", headers=exc.headers)


@app.exception_handler(StarletteHTTPException)
async def starlette_error(_: Request, exc: StarletteHTTPException):
    return _err(exc.status_code, exc.detail if isinstance(exc.detail, str) else "Request failed", headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    details = [{"field": ".".join(str(p) for p in e.get("loc", [])[1:]), "message": e.get("msg")} for e in exc.errors()]
    return _err(422, "Invalid request", details)


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    log.exception("Unhandled error: %s", type(exc).__name__)
    return _err(500, "Internal error. The incident was logged without sensitive data.")


app.include_router(router)
app.include_router(agent_gateway.router)
app.include_router(admin.router)
