// LOCAL MOCK of the Valio Aimo price/availability API for browser E2E tests ONLY.
// Results from tests that use this server are NOT evidence of live Valio integration.
// Mirrors the response shapes used by backend/app/tests/conftest.py::MockValio.
//
//   POST /__mock/config  {defaultPrice?, defaultAmount?, mode?, price?: {pid: {...}}, avail?: {pid: {...}}, reset?: true}
//   GET  /__mock/calls   -> recorded calls (API key header reported only as present/absent)
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PORT = Number(process.env.MOCK_VALIO_PORT || 8199);
const EXPECTED_KEY = process.env.MOCK_VALIO_KEY || "e2e-mock-valio-key";
const here = path.dirname(fileURLToPath(import.meta.url));
const dataFile = path.resolve(here, "../../data/valio_aimo_junction26_product_data.json");

const units = new Map();
for (const p of JSON.parse(fs.readFileSync(dataFile, "utf8"))) units.set(String(p.id), p.salesUnit);

const fresh = () => ({ defaultPrice: 2.0, defaultAmount: 100000, mode: null, price: {}, avail: {} });
let cfg = fresh();
let calls = [];

const send = (res, status, body) => {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
};

http
  .createServer((req, res) => {
    let raw = "";
    req.on("data", (c) => (raw += c));
    req.on("end", () => {
      const body = raw ? JSON.parse(raw) : {};
      const url = new URL(req.url, `http://localhost:${PORT}`);
      if (url.pathname === "/__mock/config" && req.method === "POST") {
        cfg = body.reset ? { ...fresh(), ...body } : { ...cfg, ...body };
        calls = body.reset ? [] : calls;
        return send(res, 200, { ok: true });
      }
      if (url.pathname === "/__mock/calls") return send(res, 200, calls);
      if (url.pathname === "/__mock/health") return send(res, 200, { ok: true });

      const keyOk = req.headers["x-api-key"] === EXPECTED_KEY;
      calls.push({ path: url.pathname, apiKeyPresent: !!req.headers["x-api-key"], apiKeyMatches: keyOk });
      if (!keyOk) return send(res, 401, { detail: "Invalid API key" });
      if (cfg.mode === "http500") return send(res, 500, { detail: "boom" });
      if (cfg.mode === "timeout") return; // never answers; the backend client times out
      if (url.pathname === "/api/ping") return send(res, 200, { ok: true });

      const ids = body.products || [];
      const d = body.requestedDate;
      if (url.pathname === "/api/price") {
        const products = ids
          .filter((p) => !cfg.price[p]?.missing)
          .map((p) => {
            const o = cfg.price[p] || {};
            const v = o.value ?? cfg.defaultPrice;
            return {
              productId: p,
              quantity: 1.0,
              catalogPrice: o.error ? null : { value: v, valueWithTax: +(v * 1.135).toFixed(4), validFrom: "2026-01-01", validTo: "2027-12-31", currency: "EUR" },
              tax: { taxRate: 13.5, taxValue: +(v * 0.135).toFixed(4) },
              netPriceFound: false,
              error: !!o.error,
            };
          });
        return send(res, 200, { customerId: body.customer, currency: "EUR", pricingDate: d, products });
      }
      if (url.pathname === "/api/availability") {
        const products = ids
          .filter((p) => !cfg.avail[p]?.missing)
          .map((p) => {
            const o = cfg.avail[p] || {};
            const lines = o.no_lines ? [] : [{ salesBlock: !!o.line_block, availableAmount: o.amount ?? cfg.defaultAmount, availableDate: o.date || d, salesUnit: units.get(p) || "ST" }];
            return { productId: p, closingTime: `${d}T23:59:00`, salesBlock: !!o.block, logisticModel: "Stock", scheduleLines: lines };
          });
        return send(res, 200, { timeNow: new Date().toISOString().slice(0, 19), requestedDate: `${d}T00:00:00`, products });
      }
      send(res, 404, { detail: "not found" });
    });
  })
  .listen(PORT, "127.0.0.1", () => console.log(`mock-valio listening on ${PORT} (${units.size} product units)`));
