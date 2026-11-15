// Visual review helper (isolated E2E stack only). Seeds a little real activity through the API, then captures every
// route per role at desktop and mobile widths and reports horizontal overflow. Usage:
//   node e2e/perf/screens.mjs <outDir> [--seed]
import { chromium } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { E2E } from "../e2e-env.mjs";

const OUT = process.argv[2] || "screens";
const SEED = process.argv.includes("--seed");
fs.mkdirSync(OUT, { recursive: true });
const API = E2E.apiUrl;
const MOCK = `http://127.0.0.1:${E2E.mockValioPort}`;

const ROUTES = ["/", "/catalogue", "/workspace", "/procurement", "/procurement?tab=proposals", "/approvals", "/trust", "/audit", "/policies", "/admin"];
const ROLES = { buyer: "buyer@aimo.local", manager: "manager@aimo.local", admin: "admin@aimo.local", viewer: "viewer@aimo.local" };

async function token(email) {
  const r = await fetch(`${API}/api/auth/login`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email, password: E2E.demoPassword }) });
  return (await r.json()).token;
}
async function seed() {
  const admin = await token(ROLES.admin);
  const pol = await (await fetch(`${API}/api/policies/active`, { headers: { Authorization: `Bearer ${admin}` } })).json();
  await fetch(`${API}/api/policies/${pol.id}`, { method: "PUT", headers: { Authorization: `Bearer ${admin}`, "content-type": "application/json" }, body: JSON.stringify({ duplicate_window_minutes: 0 }) });
  const buyer = await token(ROLES.buyer);
  const post = (text) => fetch(`${API}/api/procurement/requests`, { method: "POST", headers: { Authorization: `Bearer ${buyer}`, "content-type": "application/json" }, body: JSON.stringify({ text }) });
  const mock = (cfg) => fetch(`${MOCK}/__mock/config`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(cfg) });
  await mock({ reset: true, defaultPrice: 0.1 });
  await post("I need 1000 litres of lactose-free milk by Monday.");
  await mock({ reset: true, defaultPrice: 600 });
  await post("We need 120 kg of unsalted butter for Wednesday.");
  await mock({ reset: true, defaultPrice: 0.1, defaultAmount: 9999999999 });
  await post("I need 600 litres of lactose-free milk by Monday.");
  await mock({ reset: true, defaultPrice: 2.0 });
  await post("Allergen-sensitive kitchen: we need 20 kg of gluten-free pasta, it must be milk-free and egg-free, delivered by Friday.");
}

if (SEED) await seed();
const browser = await chromium.launch({ channel: process.env.PW_CHANNEL ?? "chrome", headless: true });
const report = [];
for (const [role, email] of Object.entries(ROLES)) {
  for (const [vp, size] of Object.entries({ desktop: { width: 1440, height: 900 }, mobile: { width: 390, height: 844 } })) {
    if (vp === "mobile" && role !== "buyer" && role !== "manager") continue;
    const ctx = await browser.newContext({ viewport: size });
    const page = await ctx.newPage();
    await page.goto(`${E2E.uiUrl}/login`);
    await page.fill("input[autocomplete=username]", email);
    await page.fill("input[type=password]", E2E.demoPassword);
    await page.getByRole("button", { name: "Enter AIMO PROTOCOL" }).click();
    await page.waitForURL(`${E2E.uiUrl}/`);
    for (const r of ROUTES) {
      await page.goto(`${E2E.uiUrl}${r}`);
      await page.locator("main h1").first().waitFor({ timeout: 20000 }).catch(() => {});
      await page.waitForTimeout(900);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      report.push({ role, vp, route: r, overflow_px: overflow });
      const name = `${role}-${vp}-${r === "/" ? "overview" : r.slice(1).replace(/[/?=]/g, "_")}.png`;
      await page.screenshot({ path: path.join(OUT, name), fullPage: vp === "desktop" });
    }
    await ctx.close();
  }
}
await browser.close();
fs.writeFileSync(path.join(OUT, "overflow.json"), JSON.stringify(report, null, 1));
const bad = report.filter((x) => x.overflow_px > 0);
console.log(`captured ${report.length} pages; horizontal overflow on ${bad.length}:`, bad.map((b) => `${b.role}/${b.vp}${b.route} +${b.overflow_px}px`).join(", ") || "none");
