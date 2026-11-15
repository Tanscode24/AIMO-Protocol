"""Apply database migrations (idempotent; preserves existing records).

Usage (from backend/):  python -m scripts.migrate
"""
from __future__ import annotations

from app.database import init_db


def main() -> None:
    applied = init_db()
    print("Applied:", ", ".join(applied) if applied else "nothing (schema already up to date)")


if __name__ == "__main__":
    main()
