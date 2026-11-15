import { expect, type APIRequestContext, type Browser, type Page } from "@playwright/test";
import { E2E } from "../e2e-env.mjs";

export const API = E2E.apiUrl;
export const UI = E2E.uiUrl;
const MOCK = `http://127.0.0.1:${E2E.mockValioPort}`;

export const USERS = {
  admin: "admin@aimo.local",
  manager: "manager@aimo.local",
  buyer: "buyer@aimo.local",
  buyer2: "buyer2@aimo.local",
  viewer: "viewer@aimo.local",
  nordicBuyer: "buyer@nordic.local",
  nordicManager: "manager@nordic.local",
};

/** Sign in through the real login form; returns once the dashboard has loaded. */
export async function uiLogin(page: Page, email: string) {
  await page.goto("/login");
  await page.fill("input[autocomplete=username]", email);
  await page.fill("input[type=password]", E2E.demoPassword);
  await page.getByRole("button", { name: "Enter AIMO PROTOCOL" }).click();
  await page.waitForURL(`${UI}/`);
  await expect(page.locator("main h1").first()).toBeVisible();
}

/** A separate browser context per user, so cookies never mix between roles. */
export async function pageAs(browser: Browser, email: string) {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await uiLogin(page, email);
  return page;
}

export async function apiToken(request: APIRequestContext, email: string): Promise<string> {
  const r = await request.post(`${API}/api/auth/login`, { data: { email, password: E2E.demoPassword } });
  expect(r.status(), await r.text()).toBe(200);
  return (await r.json()).token;
}

export const bearer = (t: string) => ({ Authorization: `Bearer ${t}` });

export async function mockValio(request: APIRequestContext, cfg: Record<string, unknown>) {
  const r = await request.post(`${MOCK}/__mock/config`, { data: cfg });
  expect(r.ok()).toBeTruthy();
}

export async function mockCalls(request: APIRequestContext): Promise<{ path: string; apiKeyMatches: boolean }[]> {
  return (await request.get(`${MOCK}/__mock/calls`)).json();
}

/** Default policy with the duplicate-request window disabled, so each test can submit freely. */
export async function resetPolicy(request: APIRequestContext) {
  const h = bearer(await apiToken(request, USERS.admin));
  const pol = await (await request.get(`${API}/api/policies/active`, { headers: h })).json();
  const r = await request.put(`${API}/api/policies/${pol.id}`, {
    headers: h,
    data: {
      max_order_value: 500, auto_approval_enabled: true, require_human_approval_above_limit: true,
      allowed_categories: [], blocked_categories: [], blocked_products: [], allow_substitutions: true,
      substitution_requires_approval: true, unknown_allergen_action: "BLOCK", unverified_price_action: "REQUIRE_APPROVAL",
      unverified_availability_action: "REQUIRE_APPROVAL", terminal_quantity_action: "REQUIRE_APPROVAL",
      unverified_conversion_action: "REQUIRE_APPROVAL", allow_late_delivery: false, duplicate_window_minutes: 0,
      clear_max_quantity: true, weight_price: 0.4, weight_reliability: 0.3, weight_sustainability: 0.2, weight_match: 0.1,
    },
  });
  expect(r.status(), await r.text()).toBe(200);
}

export async function procure(request: APIRequestContext, email: string, text: string) {
  const r = await request.post(`${API}/api/procurement/requests`, { headers: bearer(await apiToken(request, email)), data: { text } });
  expect(r.status(), await r.text()).toBe(200);
  return r.json();
}

export const MILK = "I need 1,000 litres of lactose-free milk by Monday. Find the best option based on price and availability.";
export const PASTA = "Allergen-sensitive kitchen: we need 20 kg of gluten-free pasta, it must be milk-free and egg-free, delivered by Friday.";
export const butter = (kg: number) => `We need ${kg} kg of unsalted butter for Wednesday.`;
