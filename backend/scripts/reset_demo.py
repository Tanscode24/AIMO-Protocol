"""Clear all procurement activity (requests, recommendations, approvals, proposals, audit events)
while keeping the ingested catalogue, users and policy. Use before a live demo.

Usage (from backend/, with the backend stopped):  python -m scripts.reset_demo
"""
from __future__ import annotations

from app.database import SessionLocal, init_db
from app.models import Approval, AuditEvent, IdempotencyRecord, Order, ProcurementRequest, Recommendation
from app.seed import seed


def main() -> None:
    init_db()
    with SessionLocal() as db:
        for model in (Order, Approval, Recommendation, IdempotencyRecord, ProcurementRequest, AuditEvent):
            n = db.query(model).delete()
            print(f"Deleted {n} {model.__tablename__}")
        db.commit()
        seed(db)
    print("Demo data reset. Catalogue, users and policy kept.")


if __name__ == "__main__":
    main()
