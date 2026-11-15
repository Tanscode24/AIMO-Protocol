import { expect, test } from "@playwright/test";
import { API, USERS, apiToken, bearer, uiLogin } from "./helpers";

test.describe("authentication & sessions", () => {
  test("login and logout through the UI; the session cookie is HttpOnly and dies server-side", async ({ page, context, request }) => {
    await uiLogin(page, USERS.buyer);
    await expect(page.getByText("own records").first()).toBeVisible();
    expect(await page.evaluate(() => document.cookie)).not.toContain("aimo_session");
    const cookie = (await context.cookies()).find((c) => c.name === "aimo_session");
    expect(cookie?.httpOnly).toBe(true);
    expect(cookie?.sameSite).toBe("Lax");

    await page.getByRole("button", { name: "Sign out" }).click();
    await page.waitForURL(/\/login/);
    // the copied cookie value no longer authenticates (revoked server-side, not just deleted in the browser)
    const r = await request.get(`${API}/api/auth/me`, { headers: { Cookie: `aimo_session=${cookie!.value}` } });
    expect(r.status()).toBe(401);
    await page.goto("/");
    await page.waitForURL(/\/login/);
  });

  test("logout invalidates a copied bearer token", async ({ request }) => {
    const token = await apiToken(request, USERS.manager);
    expect((await request.get(`${API}/api/auth/me`, { headers: bearer(token) })).status()).toBe(200);
    expect(await (await request.post(`${API}/api/auth/logout`, { headers: bearer(token) })).json()).toEqual({ ok: true });
    for (const path of ["/api/auth/me", "/api/products?q=milk", "/api/procurement/requests", "/api/approvals"]) {
      expect((await request.get(`${API}${path}`, { headers: bearer(token) })).status(), path).toBe(401);
    }
  });

  test("one session's logout leaves the user's other sessions working", async ({ request }) => {
    const a = await apiToken(request, USERS.viewer);
    const b = await apiToken(request, USERS.viewer);
    await request.post(`${API}/api/auth/logout`, { headers: bearer(a) });
    expect((await request.get(`${API}/api/auth/me`, { headers: bearer(a) })).status()).toBe(401);
    expect((await request.get(`${API}/api/auth/me`, { headers: bearer(b) })).status()).toBe(200);
  });

  test("wrong password shows a generic error, then the UI shows throttling (429)", async ({ page }) => {
    await page.goto("/login");
    const email = "nobody-e2e@aimo.local"; // unknown account: identical behaviour, and no demo user gets throttled
    await page.fill("input[autocomplete=username]", email);
    for (let i = 0; i < 5; i++) {
      await page.fill("input[type=password]", `wrong-${i}`);
      await page.getByRole("button", { name: "Enter AIMO PROTOCOL" }).click();
      await expect(page.getByText("Invalid email or password")).toBeVisible();
    }
    await page.fill("input[type=password]", "wrong-final");
    await page.getByRole("button", { name: "Enter AIMO PROTOCOL" }).click();
    await expect(page.getByText("Too many failed sign-in attempts")).toBeVisible();
  });

  test("unauthenticated requests are rejected", async ({ request }) => {
    for (const path of ["/api/products", "/api/procurement/requests", "/api/audit", "/api/admin/users"]) {
      expect((await request.get(`${API}${path}`)).status(), path).toBe(401);
    }
  });
});
