import { expect, test } from "@playwright/test";
import { USERS, butter, mockValio, pageAs, procure, resetPolicy, uiLogin } from "./helpers";

// UI/UX redesign coverage: guided request flow, navigation shell, responsive layout, keyboard and dialogs.
test.describe("redesigned experience", () => {
  test.beforeAll(async ({ request }) => resetPolicy(request));

  test("guided request: 5 steps, review shows AIMO's interpretation, submit yields a policy decision", async ({ page, request }) => {
    await mockValio(request, { reset: true, defaultPrice: 0.1 });
    await uiLogin(page, USERS.buyer);
    await page.goto("/workspace");
    await expect(page.getByRole("heading", { name: "New procurement request" })).toBeVisible();
    const next = page.getByRole("button", { name: "Continue" });
    await expect(next).toBeDisabled(); // validation: product required
    await page.getByLabel("What do you need?").fill("semi-skimmed milk");
    await next.click();
    await page.getByLabel("Quantity").fill("900");
    await page.getByLabel("Unit").selectOption("L");
    await next.click();
    await expect(page.getByLabel("Delivery date")).toHaveValue(/\d{4}-\d{2}-\d{2}/);
    await next.click();
    await page.getByText("Lactose-free", { exact: true }).click();
    await page.getByText("eggs", { exact: true }).click();
    await next.click();
    await expect(page.getByText(/We need 900 litres of lactose-free semi-skimmed milk, delivered by \d{4}-\d{2}-\d{2}\. It must be egg-free\./)).toBeVisible();
    await expect(page.getByText("How AIMO understood it")).toBeVisible();
    await expect(page.getByText("Lactose-free required").first()).toBeVisible(); // parsed back by the backend
    await page.getByRole("button", { name: "Run procurement agent" }).click();
    await expect(page.getByText(/Policy decision:/)).toBeVisible({ timeout: 60_000 });
    await expect(page).toHaveURL(/\/workspace\?request=req_/);
    await expect(page.getByText(/NOT SUBMITTED TO SUPPLIER/).first()).toBeVisible();
  });

  test("navigation: grouped sidebar, collapse keeps accessible names, breadcrumb and active state", async ({ page }) => {
    await uiLogin(page, USERS.admin);
    const nav = page.getByRole("navigation", { name: "Primary" });
    for (const label of ["Overview", "Product Discovery", "Procurement Requests", "Proposals", "Approvals", "Agent Gateway", "Trust Center", "Audit Trail", "Users & Roles", "Policies"]) {
      await expect(nav.getByRole("link", { name: new RegExp(`^${label.replace("&", "\\&")}`) }).first()).toBeVisible(); // Approvals also announces its pending count
    }
    await nav.getByRole("link", { name: "Trust Center" }).click();
    await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toContainText("Trust Center");
    await expect(nav.getByRole("link", { name: "Trust Center" })).toHaveAttribute("aria-current", "page");
    await page.getByRole("button", { name: "Collapse sidebar" }).click();
    await expect(nav.getByRole("link", { name: "Audit Trail" })).toBeVisible(); // icon-only, name via aria-label
    await nav.getByRole("link", { name: "Audit Trail" }).click();
    await expect(page.locator("main h1")).toHaveText("Audit Trail");
    await page.getByRole("button", { name: "Expand sidebar" }).click();
  });

  test("mobile: menu drawer navigation and no horizontal overflow on every route", async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await ctx.newPage();
    await uiLogin(page, USERS.manager);
    await page.getByRole("button", { name: "Open menu" }).click();
    await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Approvals" }).click();
    await expect(page.getByRole("heading", { name: "Approval Center" })).toBeVisible();
    for (const path of ["/", "/catalogue", "/workspace", "/procurement", "/procurement?tab=proposals", "/approvals", "/trust", "/audit", "/policies"]) {
      await page.goto(path);
      await expect(page.locator("main h1").first()).toBeVisible();
      await page.waitForTimeout(400);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow, `${path} horizontal overflow`).toBeLessThanOrEqual(0);
    }
    await ctx.close();
  });

  test("keyboard: tabs move with arrow keys; the skip link focuses main", async ({ page }) => {
    await uiLogin(page, USERS.buyer);
    await page.goto("/workspace");
    await page.getByRole("tab", { name: "Guided request" }).focus();
    await page.keyboard.press("ArrowRight");
    await expect(page.getByRole("tab", { name: "Describe in your own words" })).toHaveAttribute("aria-selected", "true");
    await page.goto("/");
    await page.keyboard.press("Tab");
    await expect(page.locator(":focus")).toHaveText("Skip to main content");
  });

  test("destructive actions ask for confirmation and can be cancelled", async ({ browser, request }) => {
    await mockValio(request, { reset: true, defaultPrice: 600 });
    const d = await procure(request, USERS.buyer, butter(170));
    const m = await pageAs(browser, USERS.manager);
    await m.goto("/approvals");
    await m.getByRole("button", { name: new RegExp(d.human_approval.approval_id) }).click();
    await m.getByRole("button", { name: "Reject" }).click();
    const dialog = m.getByRole("alertdialog");
    await expect(dialog).toBeVisible();
    await m.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(m.getByRole("button", { name: /Approve & revalidate/ })).toBeEnabled(); // still pending
  });
});
