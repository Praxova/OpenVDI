import { test, expect } from "@playwright/test";

test.describe("connect flow", () => {
  test.beforeEach(async ({ context }) => {
    // Bypass login by pre-seeding the auth user in localStorage.
    // M3-03's AuthProvider reads `openvdi.auth.user` on mount; if
    // it's a valid DevUser shape, we go straight to the protected
    // tree on first navigation.
    const username = process.env.OPENVDI_TEST_USER ?? "alice";
    const groups = (process.env.OPENVDI_TEST_GROUPS ?? "")
      .split(",")
      .map((g) => g.trim())
      .filter((g) => g !== "");
    await context.addInitScript(
      ({ username, groups }) => {
        window.localStorage.setItem(
          "openvdi.auth.user",
          JSON.stringify({ username, groups, role: "user" }),
        );
      },
      { username, groups },
    );
  });

  test("connect → canvas paints → disconnect → cache invalidated → session in history", async ({
    page,
  }) => {
    await page.goto("/desktops");

    // Single pool card; click Connect (or Resume — either is acceptable
    // for the assertion since both navigate to the same console route).
    const connectLink = page.getByRole("link", {
      name: /^(Connect|Resume) /i,
    });
    await expect(connectLink).toBeVisible();
    await connectLink.click();

    // URL transitions to /desktops/{poolId}/console.
    await expect(page).toHaveURL(/\/desktops\/[^/]+\/console$/);

    // Connection indicator goes through "Connecting…" → "Connected to ...".
    // The toolbar's status region (role="status") is the source of truth.
    const toolbar = page.getByRole("toolbar", { name: /Console controls/i });
    const status = toolbar.getByRole("status");

    await expect(status).toContainText(/Connecting/i, { timeout: 5_000 });
    // Connected can take ~10-30s on a fresh clone. Generous timeout.
    await expect(status).toContainText(/^Connected to /, { timeout: 30_000 });

    // The viewer mounts a <canvas> inside its container. Assert it's
    // visible and has non-zero dimensions.
    //
    // Per the M3 risks table: "canvas exists + non-zero w/h + RFB.connect
    // event fired (NOT pixel content)." The "Connected" status above
    // is the transitive proof of the connect event; the bounding-box
    // assertion below is the rendered-canvas proof.
    const canvas = page.locator("canvas").first();
    await expect(canvas).toBeVisible();
    const box = await canvas.boundingBox();
    expect(box, "canvas should have a bounding box").not.toBeNull();
    expect(box!.width).toBeGreaterThan(0);
    expect(box!.height).toBeGreaterThan(0);

    // Disconnect. The page should navigate back to /desktops and the
    // launcher's TanStack cache should refetch (M3-06's onSettled
    // invalidation hits desktopsKeys.all).
    const refetchPromise = page.waitForRequest(
      (req) =>
        req.url().includes("/api/v1/me/desktops") &&
        !req.url().includes("/connect") &&
        req.method() === "GET",
    );

    await toolbar.getByRole("button", { name: /^Disconnect/i }).click();
    await expect(page).toHaveURL(/\/desktops$/, { timeout: 10_000 });
    await refetchPromise;

    // The launcher heading is back.
    await expect(
      page.getByRole("heading", { level: 1, name: "Your desktops" }),
    ).toBeVisible();

    // Navigate to /sessions, toggle filter to "All", verify the
    // disconnected session is in the table. This validates M3-07's
    // wiring end-to-end.
    await page.getByRole("link", { name: /^Sessions$/ }).click();
    await expect(page).toHaveURL(/\/sessions$/);

    // The "All" filter button must NOT be currently pressed (we open
    // on Active by default). Click it to flip.
    const allFilter = page.getByRole("button", {
      name: /^All$/,
      pressed: false,
    });
    await allFilter.click();

    // The table now contains a row whose status badge reads
    // "Disconnected". scope by role=row + filter to avoid matching
    // headers etc.
    await expect(
      page.getByRole("row").filter({ hasText: /Disconnected/i }),
    ).toBeVisible();
  });
});
