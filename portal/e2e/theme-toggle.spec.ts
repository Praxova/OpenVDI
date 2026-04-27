import { test, expect } from "@playwright/test";

test.describe("theme toggle", () => {
  test.beforeEach(async ({ context }) => {
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
        // Force a known starting theme so the test is deterministic
        // regardless of the test runner's prefers-color-scheme setting.
        window.localStorage.setItem("openvdi.theme", "light");
      },
      { username, groups },
    );
  });

  test("toggle flips data-theme and the launcher remains visible", async ({
    page,
  }) => {
    await page.goto("/desktops");
    await expect(
      page.getByRole("heading", { level: 1, name: "Your desktops" }),
    ).toBeVisible();

    const html = page.locator("html");
    await expect(html).toHaveAttribute("data-theme", "light");

    // Pool card visible in light mode.
    await expect(
      page.locator("article").filter({
        has: page.getByRole("link", { name: /Connect|Resume/i }),
      }),
    ).toBeVisible();

    // Toggle. The theme button is in the AppShell header. Its
    // accessible name reads "Switch to dark mode" / "Switch to light
    // mode" depending on current state — matched by the broad regex.
    await page.getByRole("button", { name: /Switch to (dark|light) mode/i }).click();
    await expect(html).toHaveAttribute("data-theme", "dark");

    // Pool card still visible in dark mode (no rendering crashes
    // on mode flip; the design tokens cover both modes).
    await expect(
      page.locator("article").filter({
        has: page.getByRole("link", { name: /Connect|Resume/i }),
      }),
    ).toBeVisible();

    // Toggle back to verify the round-trip.
    await page.getByRole("button", { name: /theme/i }).click();
    await expect(html).toHaveAttribute("data-theme", "light");
  });
});
