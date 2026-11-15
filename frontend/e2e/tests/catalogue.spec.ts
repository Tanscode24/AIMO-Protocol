import { expect, test } from "@playwright/test";
import { API, USERS, apiToken, bearer, mockCalls, mockValio, uiLogin } from "./helpers";

// Price/availability here come from the LOCAL MOCK Valio server (e2e/mock-valio.mjs), not the live API.
test.describe("catalogue, search and price/availability verification (mock Valio)", () => {
  test.beforeEach(async ({ request }) => mockValio(request, { reset: true, defaultPrice: 2.0 }));

  test("product search finds products by text and by product code", async ({ page }) => {
    await uiLogin(page, USERS.buyer);
    await page.goto("/catalogue");
    const search = page.getByLabel("Search products");
    await search.fill("lactose free milk");
    await expect(page.locator("main button.card").first()).toBeVisible();
    await expect(page.locator("main")).toContainText(/lactose/i);
    await search.fill("464201");
    await expect(page.locator("main button.card")).toHaveCount(1);
  });

  test("live check shows VERIFIED price and AVAILABLE stock; backend sends X-API-Key", async ({ page, request }) => {
    await uiLogin(page, USERS.buyer);
    await page.goto("/catalogue");
    await page.getByLabel("Search products").fill("464201");
    await page.locator("main button.card").first().click();
    await page.getByRole("button", { name: "Query Valio API" }).click();
    // exact values served by the mock prove the panel shows the verified live result, not catalogue data
    const body = page.locator("body");
    await expect(body).toContainText(/Price\s*Verified\s*Excl\. VAT\s*€2\.00/);
    await expect(body).toContainText(/Availability\s*Available\s*100000 ST available/);
    const calls = await mockCalls(request);
    expect(calls.some((c) => c.path === "/api/price")).toBeTruthy();
    expect(calls.every((c) => c.apiKeyMatches)).toBeTruthy();
  });

  test("missing price and empty availability are never reported as verified/available", async ({ request }) => {
    await mockValio(request, { reset: true, price: { "464201": { error: true } }, avail: { "464201": { no_lines: true } } });
    const h = bearer(await apiToken(request, USERS.buyer));
    const r = await request.post(`${API}/api/products/464201/live-check`, { headers: h, data: { requested_date: "2026-12-16", quantity: 10 } });
    expect(r.status()).toBe(200);
    const d = await r.json();
    expect(d.price.status).not.toBe("VERIFIED");
    expect(d.availability.status).not.toBe("AVAILABLE");
  });

  test("terminal 999999 availability is not treated as confirmed stock", async ({ request }) => {
    await mockValio(request, { reset: true, avail: { "464201": { amount: 999999 } } });
    const h = bearer(await apiToken(request, USERS.buyer));
    const d = await (await request.post(`${API}/api/products/464201/live-check`, { headers: h, data: { requested_date: "2026-12-16", quantity: 10 } })).json();
    expect(d.availability.status).toBe("AVAILABLE_QTY_UNCONFIRMED");
  });

  test("Stock sentinel 9999999999 is shown as NOT confirmed stock, never as 1e+10", async ({ page, request }) => {
    await mockValio(request, { reset: true, defaultPrice: 2.0, avail: { "464201": { amount: 9999999999 } } });
    await uiLogin(page, USERS.buyer);
    await page.goto("/catalogue");
    await page.getByLabel("Search products").fill("464201");
    await page.locator("main button.card").filter({ hasText: "464201" }).first().waitFor();
    await page.waitForFunction(() => document.querySelectorAll("main button.card").length === 1);
    await page.locator("main button.card").first().click();
    await page.getByRole("button", { name: "Query Valio API" }).click();
    const body = page.locator("body");
    await expect(body).toContainText(/Availability\s*NOT CONFIRMED — Human review required/);
    await expect(body).toContainText("NOT CONFIRMED — supplier availability requires human review.");
    const text = await body.innerText();
    expect(text).not.toContain("1e+10");
    expect(text).not.toContain("9,999,999,999");
    expect(text).not.toMatch(/9999999999 ST available on/);
  });
});
