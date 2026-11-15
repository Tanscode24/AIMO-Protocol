// LIVE Valio procurement journey through the real frontend (manual, opt-in; NOT part of `npm run e2e`).
// Uses the REAL Valio API via the backend; makes a handful of price/availability calls; places NO order
// (the Valio API has no order endpoint). Credentials come from the environment and are never printed.
//
//   LIVE_BASE_URL=https://aimo.example.com LIVE_BUYER_EMAIL=… LIVE_BUYER_PASSWORD=… \
//   LIVE_MANAGER_EMAIL=… LIVE_MANAGER_PASSWORD=… [LIVE_IGNORE_TLS=1] [LIVE_OUT=dir] \
//   node e2e/live/live-journey.mjs
import { chromium } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const env = (k) => {
  const v = process.env[k];
  if (!v) throw new Error(`missing ${k}`);
  return v;
};
const BASE = env("LIVE_BASE_URL");
const OUT = process.env.LIVE_OUT || "live-journey-output";
const PRODUCT = process.env.LIVE_PRODUCT || "464201";
const TEXT = process.env.LIVE_TEXT || "We need 1000 litres of lactose-free milk by Wednesday.";
fs.mkdirSync(OUT, { recursive: true });

const log = [];
const step = (name, data = {}) => {
  log.push({ step: name, at: new Date().toISOString(), ...data });
  console.log(`✔ ${name}`, Object.keys(data).length ? JSON.stringify(data) : "");
};
const shot = (page, name) => page.screenshot({ path: path.join(OUT, `${String(log.length).padStart(2, "0")}-${name}.png`), fullPage: true });

const browser = await chromium.launch({ channel: process.env.PW_CHANNEL ?? "chrome", headless: true });
const newCtx = () => browser.newContext({ ignoreHTTPSErrors: process.env.LIVE_IGNORE_TLS === "1", viewport: { width: 1440, height: 900 } });

async function login(page, email, password) {
  await page.goto(`${BASE}/login`);
  await page.fill("input[autocomplete=username]", email);
  await page.fill("input[type=password]", password);
  await page.getByRole("button", { name: "Enter AIMO PROTOCOL" }).click();
  await page.waitForURL(`${BASE}/`);
  await page.locator("main h1").first().waitFor();
}
// API calls through the page context reuse the HttpOnly session cookie (same origin) + the CSRF header.
const api = async (page, p, init = {}) => {
  const r = await page.request.fetch(`${BASE}${p}`, { ...init, headers: { "X-Requested-With": "aimo-web", ...(init.headers || {}) } });
  if (!r.ok()) throw new Error(`${p} -> ${r.status()} ${await r.text()}`);
  return r.json();
};

try {
  // 1-2. buyer signs in through the real UI
  const bctx = await newCtx();
  const buyer = await bctx.newPage();
  await login(buyer, env("LIVE_BUYER_EMAIL"), env("LIVE_BUYER_PASSWORD"));
  const cookie = (await bctx.cookies()).find((c) => c.name === "aimo_session");
  step("buyer signed in", { secure_cookie: cookie?.secure, http_only: cookie?.httpOnly, same_site: cookie?.sameSite });
  await shot(buyer, "buyer-dashboard");

  // 3. search for a real product
  await buyer.goto(`${BASE}/catalogue`);
  await buyer.getByLabel("Search products").fill("lactose free milk");
  await buyer.locator("main button.card").first().waitFor();
  await buyer.getByLabel("Search products").fill(PRODUCT);
  // wait until the result list has been replaced by the single matching product (the search is debounced)
  await buyer.locator("main button.card").filter({ hasText: PRODUCT }).first().waitFor();
  await buyer.waitForFunction(() => document.querySelectorAll("main button.card").length === 1);
  await buyer.locator("main button.card").filter({ hasText: PRODUCT }).click();
  await buyer.getByText(PRODUCT).first().waitFor();
  const product = await api(buyer, `/api/products/${PRODUCT}`);
  step("product found", { id: product.id, name: product.display_name || product.name, sales_unit: product.sales_unit });
  await shot(buyer, "product");

  // 4-5. live price + availability from the UI
  await buyer.getByRole("button", { name: "Query Valio API" }).click();
  await buyer.getByText(/Excl\. VAT/).first().waitFor({ timeout: 30000 });
  const body = await buyer.locator("body").innerText();
  const panelTitle = body.match(new RegExp(`[^\\n]*${PRODUCT}[^\\n]*`))?.[0] ?? "";
  const panel = body.match(/price\s*\n[\s\S]{0,500}?order cut-off[^\n]*\n?[^\n]*/i)?.[0] ?? "";
  step("live check rendered in UI", { panel_title: panelTitle.trim(), ui_excerpt: panel.replace(/\s+/g, " ").slice(0, 400) });
  await shot(buyer, "live-check");

  // 6-7. procurement request -> proposal / approval
  await buyer.goto(`${BASE}/workspace`);
  await buyer.getByRole("tab", { name: "Describe in your own words" }).click();
  await buyer.locator("main textarea").fill(TEXT);
  await buyer.getByRole("button", { name: "Run procurement agent" }).click();
  await buyer.getByText(/Policy decision:/).waitFor({ timeout: 90000 });
  const rid = (await buyer.locator("body").innerText()).match(/req_[0-9a-f]{16}/)[0];
  await shot(buyer, "decision");
  const doc = await api(buyer, `/api/procurement/requests/${rid}`);
  const sel = doc.selected || {};
  step("decision", {
    request_id: rid, decision: doc.decision, status: doc.status, reasons: (doc.reasons || []).map((r) => r.code ?? r),
    product_id: sel.product_id, quantity: sel.quantity, sales_unit: sel.sales_unit, unit_price_excl_tax: sel.unit_price_excl_tax,
    total_excl_tax: sel.total_excl_tax, currency: sel.currency, price_status: sel.price_status, availability_status: sel.availability_status,
    supplier_order_placed: doc.supplier_order_placed, parsed_quantity: doc.intent?.quantity, parsed_unit: doc.intent?.unit,
    delivery_date: doc.intent?.delivery_date,
  });

  // 8. evidence & provenance
  const ev = (doc.evidence || []).map((e) => ({ kind: e.kind || e.type, state: e.state, source: e.source, live: e.live, at: e.fetched_at || e.at }));
  step("evidence & provenance", { summary: doc.summary, evidence: ev.slice(0, 8), evidence_total: ev.length });

  // 9-10. manager approval through the UI (only when policy requires it)
  let approval = null;
  if (doc.human_approval?.approval_id) {
    const mctx = await newCtx();
    const mgr = await mctx.newPage();
    await login(mgr, env("LIVE_MANAGER_EMAIL"), env("LIVE_MANAGER_PASSWORD"));
    await mgr.goto(`${BASE}/approvals`);
    await mgr.getByRole("button", { name: new RegExp(doc.human_approval.approval_id) }).click();
    await mgr.getByLabel("Decision reason").fill("Live journey validation: approve proposal (no supplier order).");
    await shot(mgr, "approval-before");
    await mgr.getByRole("button", { name: /Approve & revalidate/ }).click();
    await mgr.getByText(/Policy approval recorded|revalidation found new problems/).waitFor({ timeout: 90000 });
    await shot(mgr, "approval-after");
    approval = await api(mgr, `/api/approvals/${doc.human_approval.approval_id}`);
    step("manager approval", { status: approval.status, revalidation_price: approval.revalidation?.price?.status,
      revalidation_availability: approval.revalidation?.availability?.status, decided_by: approval.decided_by_name });
    // 11. audit trail
    const audit = await api(mgr, `/api/audit?request_id=${rid}&page_size=200`);
    step("audit events", { count: audit.total, actions: audit.items.map((e) => e.action).reverse() });
    await mctx.close();
  } else {
    const audit = await api(buyer, `/api/audit?request_id=${rid}&page_size=200`);
    step("audit events", { count: audit.total, actions: audit.items.map((e) => e.action).reverse() });
  }
  const final = await api(buyer, `/api/procurement/requests/${rid}`);
  step("final state", { status: final.status, proposal_status: final.proposal?.status ?? null,
    submitted_to_supplier: final.proposal?.submitted_to_supplier ?? null, supplier_order_placed: final.supplier_order_placed });
  await bctx.close();
} catch (e) {
  step("FAILED", { error: String(e).slice(0, 400) });
  process.exitCode = 1;
} finally {
  fs.writeFileSync(path.join(OUT, "journey.json"), JSON.stringify(log, null, 1));
  await browser.close();
}
