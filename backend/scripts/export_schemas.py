"""Write the published JSON Schema artifacts and the OpenAPI document to the repository.

Usage (from backend/):  python -m scripts.export_schemas
Outputs:  ../schemas/*.json  and  ../schemas/aimo-openapi.json
A test (test_agent_gateway.py::test_published_schemas_are_current) fails if these files drift from the code.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.schemas.agent import SCHEMAS, json_schema

OUT = Path(__file__).resolve().parents[2] / "schemas"


def build() -> dict[str, dict]:
    from app.main import app

    files = {f"{name}.json": json_schema(name) for name in SCHEMAS}
    files["aimo-openapi.json"] = app.openapi()
    return files


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for fname, content in build().items():
        (OUT / fname).write_text(json.dumps(content, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("wrote", OUT / fname)


if __name__ == "__main__":
    main()
