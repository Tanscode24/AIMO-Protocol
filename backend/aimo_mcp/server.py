"""Minimal MCP (Model Context Protocol) server for AIMO PROTOCOL — stdio transport, JSON-RPC 2.0.

It is a thin adapter over the public agent gateway (/api/agent/v1). It holds NO special privileges:
every tool call is made over HTTP with the agent's own scoped key, so all authorization, tenant isolation,
rate limiting, policy evaluation and audit happen in AIMO exactly as for any other agent.

Run:
  AIMO_BASE_URL=http://localhost:8000 AIMO_AGENT_KEY=aimo_ak_... python -m aimo_mcp.server

Example MCP client configuration (e.g. Claude Desktop "mcpServers"):
  {"aimo": {"command": "python", "args": ["-m", "aimo_mcp.server"], "cwd": "<repo>/backend",
            "env": {"AIMO_BASE_URL": "http://localhost:8000", "AIMO_AGENT_KEY": "aimo_ak_..."}}}
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from typing import Any

import httpx

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "aimo-protocol", "version": "1.2.0"}

TOOLS = [
    {"name": "search_catalogue",
     "description": "Search the official Valio Aimo product catalogue (static data; no live prices).",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "minLength": 1}},
                     "required": ["query"]}},
    {"name": "procure",
     "description": ("Submit a natural-language procurement request. AIMO verifies price and availability live, ranks "
                     "options and applies the organization's policy. Returns ALLOWED (internal proposal, NOT submitted "
                     "to supplier), REQUIRES_APPROVAL (a human must approve) or BLOCKED, with reasons and evidence. "
                     "AIMO never places supplier orders."),
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string", "minLength": 3},
         "confirm_assumptions": {"type": "boolean", "default": False,
                                 "description": "Set true only after the human confirmed quantity/unit/date assumptions"},
         "idempotency_key": {"type": "string"}}, "required": ["text"]}},
    {"name": "get_decision",
     "description": "Fetch the current decision document for a request this agent created.",
     "inputSchema": {"type": "object", "properties": {"request_id": {"type": "string"}}, "required": ["request_id"]}},
]


def _summarise(doc: dict) -> dict:
    sel = doc.get("selected") or {}
    return {"request_id": doc.get("request_id"), "decision": doc.get("decision"), "status": doc.get("status"),
            "reasons": [f"{r['outcome']}: {r['message']}" for r in doc.get("reasons", [])],
            "selected": sel, "human_approval": doc.get("human_approval"), "proposal": doc.get("proposal"),
            "supplier_order_placed": doc.get("supplier_order_placed"), "disclaimer": doc.get("disclaimer"),
            "confirmation_required": (doc.get("intent") or {}).get("confirmation_required", [])}


class AimoMcp:
    def __init__(self, base_url: str, agent_key: str, http: httpx.Client | None = None):
        self.http = http or httpx.Client(base_url=base_url.rstrip("/"), timeout=60)
        self.headers = {"X-Agent-Key": agent_key}

    def _call(self, method: str, path: str, **kw) -> Any:
        h = {**self.headers, "X-Correlation-ID": f"mcp_{uuid.uuid4().hex[:16]}", **kw.pop("headers", {})}
        r = self.http.request(method, path, headers=h, **kw)
        body = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(f"AIMO {r.status_code} {body.get('code', '')}: {body.get('error', '')}")
        return body

    def call_tool(self, name: str, args: dict) -> Any:
        if name == "search_catalogue":
            res = self._call("GET", "/api/agent/v1/catalogue/search", params={"q": args["query"], "page_size": 10})
            return [{"id": p["id"], "name": p["name"], "brand": p["brand"], "sales_unit": p["sales_unit"],
                     "allergen_data": p["allergen_data_status"]} for p in res["items"]]
        if name == "procure":
            headers = {"Idempotency-Key": args["idempotency_key"]} if args.get("idempotency_key") else {}
            doc = self._call("POST", "/api/agent/v1/procure", headers=headers,
                             json={"text": args["text"], "confirm_assumptions": bool(args.get("confirm_assumptions"))})
            return _summarise(doc)
        if name == "get_decision":
            return _summarise(self._call("GET", f"/api/agent/v1/decisions/{args['request_id']}"))
        raise KeyError(name)

    def handle(self, msg: dict) -> dict | None:
        mid, method = msg.get("id"), msg.get("method")
        if mid is None:  # notification (e.g. notifications/initialized)
            return None
        try:
            if method == "initialize":
                result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                p = msg.get("params") or {}
                try:
                    out = self.call_tool(p.get("name"), p.get("arguments") or {})
                    result = {"content": [{"type": "text", "text": json.dumps(out, indent=2)}], "isError": False}
                except KeyError:
                    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unknown tool {p.get('name')}"}}
                except Exception as e:  # tool errors are reported to the model, not as protocol errors
                    result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
            return {"jsonrpc": "2.0", "id": mid, "result": result}
        except Exception as e:  # pragma: no cover
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}


def main() -> None:  # pragma: no cover - stdio loop
    key = os.environ.get("AIMO_AGENT_KEY", "")
    if not key:
        print("AIMO_AGENT_KEY is required", file=sys.stderr)
        sys.exit(2)
    server = AimoMcp(os.environ.get("AIMO_BASE_URL", "http://localhost:8000"), key)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}) + "\n")
            sys.stdout.flush()
            continue
        resp = server.handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
