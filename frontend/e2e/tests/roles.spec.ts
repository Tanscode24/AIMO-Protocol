import { expect, test } from "@playwright/test";
import { API, USERS, apiToken, bearer, butter, pageAs, resetPolicy, uiLogin } from "./helpers";

test.describe("role-based UI and API enforcement", () => {
  test.beforeAll(async ({ request }) => resetPolicy(request));

  test("BUYER dashboard: own-records scope, no admin navigation, read-only policies", async ({ page }) => {
    await uiLogin(page, USERS.buyer);
    await expect(page.getByText("own records").first()).toBeVisible();
    await expect(page.getByRole("link", { name: "Agent Gateway" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Users & Roles" })).toHaveCount(0);
    for (const path of ["/", "/workspace", "/catalogue", "/procurement", "/approvals", "/audit"]) {
      await page.goto(path);
      await expect(page.locator("main h1").first(), path).toBeVisible();
    }
    await page.goto("/policies");
    await expect(page.getByText("Read-only: only Administrators")).toBeVisible();
  });

  test("VIEWER is read-only in the UI and the API", async ({ page, request }) => {
    await uiLogin(page, USERS.viewer);
    await page.goto("/workspace");
    await expect(page.getByText("Read-only access")).toBeVisible();
    await page.getByRole("tab", { name: "Describe in your own words" }).click();
    await expect(page.getByRole("button", { name: "Run procurement agent" })).toBeDisabled();
    const h = bearer(await apiToken(request, USERS.viewer));
    expect((await request.post(`${API}/api/procurement/requests`, { headers: h, data: { text: butter(11) } })).status()).toBe(403);
    expect((await request.get(`${API}/api/procurement/requests`, { headers: h })).status()).toBe(200);
  });

  test("ADMIN can manage identities and verify the audit chain", async ({ browser }) => {
    const page = await pageAs(browser, USERS.admin);
    await page.getByRole("link", { name: "Agent Gateway" }).click();
    await expect(page.getByText("Agent identities")).toBeVisible();
    await page.goto("/audit");
    await page.getByRole("button", { name: "Verify chain integrity" }).click();
    await expect(page.getByText(/Valid ·/)).toBeVisible();
  });

  test("non-admins cannot reach admin APIs", async ({ request }) => {
    for (const who of [USERS.buyer, USERS.manager, USERS.viewer]) {
      const h = bearer(await apiToken(request, who));
      expect((await request.get(`${API}/api/admin/users`, { headers: h })).status(), who).toBe(403);
    }
  });

  test("MANAGER sees the Approval Center; BUYER and VIEWER cannot approve", async ({ browser }) => {
    const m = await pageAs(browser, USERS.manager);
    await m.goto("/approvals");
    await expect(m.getByRole("heading", { name: "Approval Center" })).toBeVisible();
    for (const who of [USERS.buyer, USERS.viewer]) {
      const me = await (await m.request.get(`${API}/api/auth/me`)).json();
      expect(me.permissions).toContain("approve"); // the manager's own cookie session
      const p = await pageAs(browser, who);
      const perms = (await (await p.request.get(`${API}/api/auth/me`)).json()).permissions;
      expect(perms, who).not.toContain("approve");
    }
  });
});
