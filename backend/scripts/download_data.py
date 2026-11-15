"""Optional: download the official datasets with your API key (follows redirects).

Usage (from backend/):  python -m scripts.download_data
Endpoints (from data/openapi.json):
  GET /api/products/download
  GET /api/delivery-reliability/download
  GET /api/carbon-footprint/download
"""
from __future__ import annotations

import sys

import httpx

from app.config import get_settings
from app.services.catalogue import CARBON_FILE, PRODUCT_FILE, RELIABILITY_FILE

TARGETS = [
    ("/api/products/download", PRODUCT_FILE),
    ("/api/delivery-reliability/download", RELIABILITY_FILE),
    ("/api/carbon-footprint/download", CARBON_FILE),
]


def main() -> int:
    s = get_settings()
    if not s.live_api_configured:
        print("VALIO_API_KEY is not set in backend/.env — nothing downloaded.")
        return 1
    s.data_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=s.valio_api_base_url, headers={"X-API-Key": s.valio_api_key.get_secret_value()},
                      follow_redirects=True, timeout=120) as c:
        for path, fname in TARGETS:
            r = c.get(path)
            if r.status_code != 200:
                print(f"{path}: HTTP {r.status_code}")
                return 1
            (s.data_dir / fname).write_bytes(r.content)
            print(f"{path} -> data/{fname} ({len(r.content):,} bytes)")
    print("Done. Run: python -m scripts.ingest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
