import { expect, test } from "@playwright/test";
import { API, USERS, apiToken, bearer, butter, mockValio, pageAs, procure, resetPolicy } from "./helpers";

test.describe("cross-user and cross-tenant isolation", () => {
  test.beforeAll(async ({ request }) => resetPolicy(request));

  test("another buyer in the same organization cannot see the request (UI + API)", async ({ browser, request }) => {
    await mockValio(request, { reset: true, defaultPrice: 600 });
    const d = await procure(request, USERS.buyer, butter(150));
    const b2 = await pageAs(browser, USERS.buyer2);
    await b2.goto(`/workspace?request=${d.request_id}`);
    await expect(b2.getByText("Request not found")).toBeVisible();
    await expect(b2.getByText("Ranked recommendations")).toHaveCount(0);
    const h = bearer(await apiToken(request, USERS.buyer2));
    expect((await request.get(`${API}/api/procurement/requests/${d.request_id}`, { headers: h })).status()).toBe(404);
    expect((await request.get(`${API}/api/approvals/${d.human_approval.approval_id}`, { headers: h })).status()).toBe(404);
  });

  test("another organization cannot see or approve the request (UI + API)", async ({ browser, request }) => {
    await mockValio(request, { reset: true, defaultPrice: 600 });
    const d = await procure(request, USERS.buyer, butter(160));
    const approvalId = d.human_approval.approval_id;
    for (const who of [USERS.nordicBuyer, USERS.nordicManager]) {
      const h = bearer(await apiToken(request, who));
      expect((await request.get(`${API}/api/procurement/requests/${d.request_id}`, { headers: h })).status(), who).toBe(404);
      const audit = await (await request.get(`${API}/api/audit?request_id=${d.request_id}`, { headers: h })).json();
      expect(audit.total, who).toBe(0);
    }
    const nh = bearer(await apiToken(request, USERS.nordicManager));
    expect((await request.post(`${API}/api/approvals/${approvalId}/approve`, { headers: nh, data: {} })).status()).toBe(404);

    const nm = await pageAs(browser, USERS.nordicManager);
    await nm.goto("/approvals");
    await expect(nm.getByRole("heading", { name: "Approval Center" })).toBeVisible();
    await expect(nm.getByText(approvalId)).toHaveCount(0); // not rendered anywhere
    // the same-organization manager does see it
    const m = await pageAs(browser, USERS.manager);
    await m.goto("/approvals");
    await expect(m.getByRole("button", { name: new RegExp(approvalId) })).toBeVisible();
  });
});
