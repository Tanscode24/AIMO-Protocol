"""Initialise the database and (re)ingest the official Valio Aimo datasets.

Usage (from backend/):  python -m scripts.ingest
"""
from __future__ import annotations

import json

from app.database import SessionLocal, init_db
from app.seed import seed
from app.services import audit
from app.services.catalogue import ingest


def main() -> None:
    init_db()
    with SessionLocal() as db:
        seed(db)
        stats = ingest(db)
        audit.record(db, "CATALOGUE_INGESTED", reason="Manual ingestion (scripts.ingest)", payload=stats, commit=True)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
