import { test, expect, type BrowserContext } from "@playwright/test";

import {
  getAdminToken,
  loginAsAdmin,
  loginAsUser,
  logout,
  readEnv,
} from "./helpers/auth";


// Per-test state captured for afterEach cleanup.
let ephemeralPoolId: string | null = null;
let userContext: BrowserContext | null = null;

// Skip the entire spec if the admin test env vars are missing —
// avoids spurious failures on contributor machines without LDAP setup.
// Same posture as `connect-flow.spec.ts`'s implicit env-var assumption,
// but explicit here.
const adminEnvSet = Boolean(
  process.env.OPENVDI_TEST_ADMIN_USER &&
    process.env.OPENVDI_TEST_ADMIN_PASSWORD &&
    process.env.OPENVDI_TEST_USER &&
    process.env.OPENVDI_TEST_USER_PASSWORD,
);


test.describe("admin happy path", () => {
  test.skip(
    !adminEnvSet,
    "Set OPENVDI_TEST_ADMIN_USER/_PASSWORD + OPENVDI_TEST_USER/_PASSWORD " +
      "to run the admin spec. See portal/README.md → 'M4 admin smoke test'.",
  );

  const env = adminEnvSet ? readEnv() : null;

  test.beforeEach(() => {
    ephemeralPoolId = null;
    userContext = null;
  });

  test.afterEach(async () => {
    // Best-effort cleanup. Failures here log a warning and don't fail
    // the test (the test already passed/failed by this point).
    if (userContext !== null) {
      try {
        await userContext.close();
      } catch {
        // Ignore — context may already be closed.
      }
    }
    if (ephemeralPoolId !== null && env !== null) {
      try {
        const token = await getAdminToken(env);
        const res = await fetch(
          `${env.brokerUrl}/api/v1/pools/${ephemeralPoolId}`,
          {
            method: "DELETE",
            headers: { Authorization: `Bearer ${token}` },
          },
        );
        if (!res.ok && res.status !== 404) {
          // eslint-disable-next-line no-console
          console.warn(
            `Cleanup: pool ${ephemeralPoolId} delete returned ${res.status}; ` +
              "manual cleanup may be needed.",
          );
        }
      } catch (err) {
        // eslint-disable-next-line no-console
        console.warn(`Cleanup: ${err}`);
      }
    }
  });

  test("login → register pool → user connects → force-disconnect → audit shows it", async ({
    page,
    browser,
  }) => {
    test.slow(); // 180s timeout for multi-context end-to-end

    if (env === null) throw new Error("env unreachable when adminEnvSet=true");

    const poolSlug = `e2e-pool-${Date.now()}`;
    const poolDisplay = `E2E ${poolSlug}`;

    // ── 1. Admin login ────────────────────────────────────────
    await loginAsAdmin(page, env);

    // Admin ▾ dropdown must be visible (FE2 role-gated).
    const adminMenu = page.getByRole("button", { name: /^Admin/i });
    await expect(adminMenu).toBeVisible();

    // ── 2. Dashboard renders ──────────────────────────────────
    await adminMenu.click();
    await page.getByRole("menuitem", { name: /^Dashboard$/i }).click();
    await expect(page).toHaveURL(/\/admin$/);
    // Each of the four cards has its own heading per M4-18.
    await expect(
      page.getByRole("heading", { name: /^Capacity$/i }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: /^Sessions$/i }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: /^Cluster Health$/i }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: /^Recent Activity$/i }),
    ).toBeVisible();

    // ── 3. Clusters page lists the test cluster ───────────────
    await adminMenu.click();
    await page.getByRole("menuitem", { name: /^Clusters$/i }).click();
    await expect(page).toHaveURL(/\/admin\/clusters$/);
    await expect(
      page.getByRole("row").filter({ hasText: env.testClusterName }),
    ).toBeVisible();

    // ── 4. Templates page lists the test template ─────────────
    await adminMenu.click();
    await page.getByRole("menuitem", { name: /^Templates$/i }).click();
    await expect(page).toHaveURL(/\/admin\/templates$/);
    await expect(
      page.getByRole("row").filter({ hasText: env.testTemplateName }),
    ).toBeVisible();

    // ── 5. Create ephemeral pool via form ─────────────────────
    await adminMenu.click();
    await page.getByRole("menuitem", { name: /^Pools$/i }).click();
    await expect(page).toHaveURL(/\/admin\/pools$/);
    await page.getByRole("link", { name: /^Add pool/i }).click();
    await expect(page).toHaveURL(/\/admin\/pools\/new$/);

    await page
      .getByLabel(/^Cluster$/)
      .selectOption({ label: env.testClusterName });
    await page
      .getByLabel(/^Template$/)
      .selectOption({ label: env.testTemplateName });
    // pool_type is a radio group; the default is "Non-persistent" but
    // click anyway for explicitness.
    await page.getByRole("radio", { name: /^Non-persistent$/i }).check();
    await page.getByLabel(/^Name \(slug\)$/).fill(poolSlug);
    await page.getByLabel(/^Display name$/).fill(poolDisplay);
    await page
      .getByLabel(/^VMID range start$/)
      .fill(String(env.testVmidStart));
    await page
      .getByLabel(/^VMID range end$/)
      .fill(String(env.testVmidStart + 9));
    await page.getByLabel(/^Min spare$/).fill("1");
    await page.getByLabel(/^Max size$/).fill("3");
    await page.getByLabel(/^Name prefix$/).fill("E2E");

    await page.getByRole("button", { name: /^Create pool$/i }).click();
    await expect(page).toHaveURL(/\/admin\/pools$/);
    const poolRow = page.getByRole("row").filter({ hasText: poolDisplay });
    await expect(poolRow).toBeVisible();

    // Capture the pool ID for cleanup. The Edit button is a real
    // <button> (it navigates programmatically), so we extract from
    // the form-page URL after clicking it later. For now, look up
    // via the broker API using the pool name.
    {
      const token = await getAdminToken(env);
      const list = await fetch(`${env.brokerUrl}/api/v1/pools`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const body = (await list.json()) as {
        data: Array<{ id: string; name: string }>;
      };
      const created = body.data.find((p) => p.name === poolSlug);
      if (created === undefined) {
        throw new Error(`Could not find created pool ${poolSlug}`);
      }
      ephemeralPoolId = created.id;
    }

    // ── 6. Provision warm spare ───────────────────────────────
    page.once("dialog", (dialog) => {
      // window.prompt asking for count; default is min_spare (1).
      void dialog.accept("1");
    });
    await poolRow
      .getByRole("button", { name: /^Provision warm spares/i })
      .click();
    // Success banner.
    await expect(
      page.getByRole("status").filter({
        hasText: new RegExp(`${poolDisplay}: provisioning`),
      }),
    ).toBeVisible();

    // Poll the row until capacity column shows >=1 available. The
    // M4-09 worker does the actual cloning; we're waiting on it.
    // Capacity column format is "1/3" for available/max.
    const capacityCell = poolRow.locator("td").nth(3);
    await expect(capacityCell).toContainText(/[1-9]\/3/, {
      timeout: 90_000,
    });

    // ── 7. Grant entitlement to test user ────────────────────
    await poolRow.getByRole("button", { name: /^Edit/i }).click();
    await expect(page).toHaveURL(
      new RegExp(`/admin/pools/${ephemeralPoolId}/edit$`),
    );

    // EntitlementsPanel: select User, type the test username, click Add.
    await page.getByLabel(/^Type$/).selectOption("user");
    await page.getByLabel(/^Username$/).fill(env.testUser);
    await page.getByRole("button", { name: /^Add$/i }).click();

    // Verify the entitlement appears in the list. The username gets
    // lowercase-coerced per M4-21's helper, so match case-insensitively.
    await expect(
      page
        .locator("li")
        .filter({ hasText: new RegExp(env.testUser, "i") }),
    ).toBeVisible();

    // ── 8. Logout → login as test user in a fresh context ─────
    await logout(page);
    userContext = await browser.newContext({ ignoreHTTPSErrors: true });
    const userPage = await userContext.newPage();
    await loginAsUser(userPage, env);

    // ── 9. User connects to the new pool ──────────────────────
    await expect(userPage).toHaveURL(/\/desktops$/);
    const userPoolCard = userPage
      .getByRole("article")
      .filter({ hasText: poolDisplay });
    await expect(userPoolCard).toBeVisible();

    const connectLink = userPoolCard.getByRole("link", {
      name: /^(Connect|Resume) /i,
    });
    await connectLink.click();
    await expect(userPage).toHaveURL(/\/desktops\/[^/]+\/console$/);

    const userToolbar = userPage.getByRole("toolbar", {
      name: /Console controls/i,
    });
    const userStatus = userToolbar.getByRole("status");
    await expect(userStatus).toContainText(/^Connected to /, {
      timeout: 60_000,
    });

    // Canvas paint proof (mirrors M3-08).
    const canvas = userPage.locator("canvas").first();
    await expect(canvas).toBeVisible();
    const box = await canvas.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.width).toBeGreaterThan(0);
    expect(box!.height).toBeGreaterThan(0);

    // ── 10. Admin force-disconnects from /admin/sessions ──────
    // Re-login admin in the original context (logout cleared it).
    await loginAsAdmin(page, env);
    await page.goto("/admin/sessions");

    // Filter to the test user; row should appear.
    await page.getByLabel(/^User$/).fill(env.testUser);
    const sessionRow = page
      .getByRole("row")
      .filter({ hasText: env.testUser });
    await expect(sessionRow.first()).toBeVisible({ timeout: 10_000 });

    // Click Force disconnect. NO confirm dialog per FE7.
    await sessionRow
      .first()
      .getByRole("button", { name: /^Force disconnect /i })
      .click();

    // Success banner.
    await expect(
      page.getByRole("status").filter({
        hasText: new RegExp(`${env.testUser}'s session ended`),
      }),
    ).toBeVisible();

    // ── 11. User's connection terminates ──────────────────────
    // Either the URL bounces back to /desktops, or the status flips
    // away from "Connected to ...". Accept either path.
    await expect(async () => {
      const url = userPage.url();
      const text = await userStatus
        .textContent({ timeout: 1000 })
        .catch(() => null);
      const ok =
        url.endsWith("/desktops") ||
        (text !== null && !/^Connected to /.test(text));
      expect(ok).toBe(true);
    }).toPass({ timeout: 10_000 });

    // ── 12. Audit page shows the force_disconnect row ─────────
    await page.goto("/admin/audit");
    await page.getByLabel(/^Action$/).fill("admin.session.force_disconnect");
    // Wait for refetch (filter change → query key change).
    await expect(
      page.getByRole("row").filter({ hasText: env.adminUser }).first(),
    ).toBeVisible({ timeout: 10_000 });

    // Click the row to open the audit drawer; verify the dialog
    // contains the disconnected user's username (rendered inside
    // the JSON details pre-block).
    await page
      .getByRole("row")
      .filter({ hasText: env.adminUser })
      .first()
      .click();
    await expect(
      page.getByRole("dialog").filter({ hasText: env.testUser }),
    ).toBeVisible({ timeout: 5_000 });
  });
});
