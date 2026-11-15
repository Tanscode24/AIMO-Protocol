// Frontend load/render measurement against the ISOLATED E2E stack (production build on :3100).
// Each sample uses a fresh browser context (cold HTTP cache). Usage (stack running):
//   node e2e/perf/frontend-perf.mjs [samples=5]
import { chromium } from "@playwright/test";
import { E2E } from "../e2e-env.mjs";

const N = Number(process.argv[2] || 5);
const browser = await chromium.launch({ channel: process.env.PW_CHANNEL ?? "chrome", headless: true });

async function measure(page) {
  return page.evaluate(async () => {
    const lcp = await new Promise((resolve) => {
      let v = null;
      new PerformanceObserver((l) => { for (const e of l.getEntries()) v = e.startTime; }).observe({ type: "largest-contentful-paint", buffered: true });
      setTimeout(() => resolve(v), 300);
    });
    const n = performance.getEntriesByType("navigation")[0];
    const res = performance.getEntriesByType("resource");
    const js = res.filter((r) => r.name.endsWith(".js"));
    return {
      ttfb: n.responseStart, dcl: n.domContentLoadedEventEnd, load: n.loadEventEnd, lcp,
      js_files: js.length, js_kb: Math.round(js.reduce((s, r) => s + (r.transferSize || 0), 0) / 1024),
      heap_mb: performance.memory ? +(performance.memory.usedJSHeapSize / 1048576).toFixed(1) : null,
    };
  });
}

const stats = (xs) => {
  const s = [...xs].filter((x) => x != null).sort((a, b) => a - b);
  return { median: +s[Math.floor(s.length / 2)].toFixed(1), min: +s[0].toFixed(1), max: +s[s.length - 1].toFixed(1) };
};

const results = {};
const record = (name, m) => (results[name] ??= []).push(m);

for (let i = 0; i < N; i++) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.goto(`${E2E.uiUrl}/login`, { waitUntil: "load" });
  record("/login (cold)", await measure(page));

  await page.fill("input[autocomplete=username]", "buyer@aimo.local");
  await page.fill("input[type=password]", E2E.demoPassword);
  const t0 = Date.now();
  await page.getByRole("button", { name: "Enter AIMO PROTOCOL" }).click();
  await page.waitForURL(`${E2E.uiUrl}/`);
  await page.locator("main h1").first().waitFor();
  await page.waitForFunction(() => !document.querySelector("main [role=status]"), null, { timeout: 30000 });
  record("login -> dashboard data rendered (ms)", { total: Date.now() - t0 });

  for (const path of ["/catalogue", "/workspace", "/approvals", "/audit"]) {
    const t = Date.now();
    await page.goto(`${E2E.uiUrl}${path}`, { waitUntil: "load" });
    await page.locator("main h1").first().waitFor();
    await page.waitForFunction(() => !document.querySelector("main [role=status]"), null, { timeout: 30000 });
    record(`${path} (full load, data rendered)`, { ...(await measure(page)), rendered: Date.now() - t });
  }
  await ctx.close();
}
await browser.close();

const out = {};
for (const [name, rows] of Object.entries(results)) {
  out[name] = Object.fromEntries(Object.keys(rows[0]).map((k) => [k, stats(rows.map((r) => r[k]))]));
}
console.log(JSON.stringify({ samples: N, target: E2E.uiUrl, browser: "Google Chrome (headless, Playwright)", results: out }, null, 1));
