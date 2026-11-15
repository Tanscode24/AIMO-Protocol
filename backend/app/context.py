"""Per-request context (correlation ID) propagated to audit events and error responses."""
from __future__ import annotations

import re
import uuid
from contextvars import ContextVar

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_SAFE = re.compile(r"^[A-Za-z0-9._:\-]{1,80}$")


def new_correlation_id(incoming: str | None = None) -> str:
    """Accept a caller-supplied correlation id only if it is short and safe; otherwise generate one."""
    if incoming and _SAFE.match(incoming):
        return incoming
    return f"cor_{uuid.uuid4().hex[:20]}"


def get_correlation_id() -> str | None:
    return correlation_id_var.get()
