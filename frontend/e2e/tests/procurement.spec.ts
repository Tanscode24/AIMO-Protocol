import { expect, test } from "@playwright/test";
import { API, MILK, PASTA, USERS, apiToken, bearer, butter, mockValio, pageAs, procure, resetPolicy } from "./helpers";

// Supplier data comes from the LOCAL MOCK Valio server. No supplier order is ever placed.
test.describe("procurement: proposals, allergens, approval, rejection, audit", () => {
  test.beforeAll(async ({ request }) => resetPolicy(request));

  test("ALLOWED request creates an internal proposal that is NOT submitted to the supplier", async ({ browser, request }) => {
    await mockValio(request, { reset: true, defaultPrice: 0.1 });
    const page = await pageAs(browser, USERS.buyer);
    await page.goto("/workspace");
    await page.getByRole("tab", { name: "Describe in your own words" }).click();
    await page.getByRole("button", { name: "Lactose-free milk, 1,000 L by Monday" }).click();
    await page.getByRole("button", { name: "Run procurement agent" }).click();
    await expect(page.getByText("Policy decision: within autonomous limits")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText(/Proposal prp_[0-9a-f]+ — NOT SUBMITTED TO SUPPLIER/)).toBeVisible();

    const d = await procure(request, USERS.buyer, MILK);
    expect(d.decision).toBe("ALLOWED");
    expect(d.selected.price_status).toBe("VERIFIED");
    expect(d.selected.availability_status).toBe("AVAILABLE");
    expect(d.proposal.status).toBe("PROPOSAL_READY_NOT_SUBMITTED");
    expect(d.proposal.submitted_to_supplier).toBe(false);
    expect(d.supplier_order_placed).toBe(false);
  });

  test("allergen restrictions exclude violating and unknown-allergen products", async ({ browser, request }) => {
    await mockValio(request, { reset: true, defaultPrice: 0.5 });
    const d = await procure(request, USERS.buyer, PASTA);
    for (const c of d.recommendations) expect(c.allergens.overall).toBe("VERIFIED_SAFE");
    expect(d.excluded.map((x: any) => x.exclusions.join(" ")).join(" ")).toContain("Excluded allergen present");

    const page = await pageAs(browser, USERS.buyer);
    await page.goto(`/workspace?request=${d.request_id}`);
    await expect(page.getByText(/Policy decision:/)).toBeVisible();
  });

  test("MANAGER approves a spend-limit request in the UI; revalidation creates a not-submitted proposal", async ({ browser, request }) => {
    await mockValio(request, { reset: true, defaultPrice: 600 });
    const buyer = await pageAs(browser, USERS.buyer);
    await buyer.goto("/workspace");
    await buyer.getByRole("tab", { name: "Describe in your own words" }).click();
    await buyer.getByRole("button", { name: "Spending limit — human approval" }).click();
    await buyer.getByRole("button", { name: "Run procurement agent" }).click();
    await expect(buyer.getByText("Policy decision: human approval required")).toBeVisible({ timeout: 60_000 });
    const rid = (await buyer.locator("body").innerText()).match(/req_[0-9a-f]{16}/)![0];

    const mh = bearer(await apiToken(request, USERS.manager));
    const doc = await (await request.get(`${API}/api/procurement/requests/${rid}`, { headers: mh })).json();
    const approvalId = doc.human_approval.approval_id;

    const manager = await pageAs(browser, USERS.manager);
    await manager.goto("/approvals");
    await manager.getByRole("button", { name: new RegExp(approvalId) }).click();
    await manager.getByLabel("Decision reason").fill("E2E approval");
    await manager.getByRole("button", { name: /Approve & revalidate/ }).click();
    await expect(manager.getByText(/NOT SUBMITTED TO SUPPLIER\. No supplier order was placed\./)).toBeVisible();

    const a = await (await request.get(`${API}/api/approvals/${approvalId}`, { headers: mh })).json();
    expect(a.status).toBe("APPROVED");
    const after = await (await request.get(`${API}/api/procurement/requests/${rid}`, { headers: mh })).json();
    expect(after.proposal.submitted_to_supplier).toBe(false);
    // single-use: a second decision is refused
    expect((await request.post(`${API}/api/approvals/${approvalId}/approve`, { headers: mh, data: {} })).status()).toBe(409);
  });

  test("MANAGER rejects a request in the UI", async ({ browser, request }) => {
    await mockValio(request, { reset: true, defaultPrice: 600 });
    const d = await procure(request, USERS.buyer, butter(130));
    expect(d.decision).toBe("REQUIRES_APPROVAL");
    const approvalId = d.human_approval.approval_id;
    const manager = await pageAs(browser, USERS.manager);
    await manager.goto("/approvals");
    await manager.getByRole("button", { name: new RegExp(approvalId) }).click();
    await manager.getByRole("button", { name: "Reject" }).click();
    await expect(manager.getByRole("alertdialog")).toContainText("Reject this request?");
    await manager.getByRole("button", { name: "Confirm rejection" }).click();
    await expect(manager.getByText("Decision recorded: REJECTED.")).toBeVisible();
    const mh = bearer(await apiToken(request, USERS.manager));
    const doc = await (await request.get(`${API}/api/procurement/requests/${d.request_id}`, { headers: mh })).json();
    expect(doc.proposal).toBeNull();
  });

  test("separation of duties: a manager cannot approve their own request; buyers cannot approve", async ({ request }) => {
    await mockValio(request, { reset: true, defaultPrice: 600 });
    const own = await procure(request, USERS.manager, butter(140));
    const mh = bearer(await apiToken(request, USERS.manager));
    const r = await request.post(`${API}/api/approvals/${own.human_approval.approval_id}/approve`, { headers: mh, data: {} });
    expect(r.status()).toBe(403);
    expect((await r.json()).error).toContain("Separation of duties");
    const bh = bearer(await apiToken(request, USERS.buyer));
    expect((await request.post(`${API}/api/approvals/${own.human_approval.approval_id}/approve`, { headers: bh, data: {} })).status()).toBe(403);
  });

  test("audit trail records the workflow and the hash chain verifies", async ({ browser, request }) => {
    await mockValio(request, { reset: true, defaultPrice: 0.1 });
    const d = await procure(request, USERS.buyer, "I need 900 litres of lactose-free milk by Monday.");
    const ah = bearer(await apiToken(request, USERS.admin));
    const ev = await (await request.get(`${API}/api/audit?request_id=${d.request_id}&page_size=100`, { headers: ah })).json();
    const actions = ev.items.map((e: any) => e.action);
    expect(actions.length).toBeGreaterThanOrEqual(3);
    expect(actions).toContain("POLICY_EVALUATED");
    const v = await (await request.get(`${API}/api/admin/audit/verify`, { headers: ah })).json();
    expect(v.ok).toBe(true);

    const admin = await pageAs(browser, USERS.admin);
    await admin.goto("/audit");
    await expect(admin.locator("main")).toContainText(d.request_id.slice(0, 8));
  });

  test("cheap request on a Stock sentinel requires human approval (quantity not confirmed)", async ({ browser, request }) => {
    // every product's Stock line returns the sentinel (cheap: without it this request would be ALLOWED)
    await mockValio(request, { reset: true, defaultPrice: 0.1, defaultAmount: 9999999999 });
    const d = await procure(request, USERS.buyer, "I need 700 litres of lactose-free milk by Monday.");
    expect(d.decision).toBe("REQUIRES_APPROVAL");
    expect(d.reasons.map((r: any) => r.code)).toContain("QUANTITY_UNCONFIRMED");
    expect(d.selected.availability_status).toBe("AVAILABLE_QTY_UNCONFIRMED");
    expect(d.proposal).toBeNull();
    expect(d.supplier_order_placed).toBe(false);
    const page = await pageAs(browser, USERS.buyer);
    await page.goto(`/workspace?request=${d.request_id}`);
    await expect(page.getByText("Policy decision: human approval required")).toBeVisible();
    await expect(page.locator("body")).toContainText("NOT CONFIRMED — supplier availability requires human review.");
    expect(await page.locator("body").innerText()).not.toContain("1e+10");
  });
});
