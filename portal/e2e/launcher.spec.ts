import { test, expect } from "@playwright/test";

test.describe("launcher", () => {
  test("login renders the launcher and logout returns to login", async ({
    page,
  }) => {
    const username = process.env.OPENVDI_TEST_USER ?? "alice";
    const groups = process.env.OPENVDI_TEST_GROUPS ?? "";

    await page.goto("/login");

    // Login form: username, groups, role pill-radio (User / Admin).
    await page.getByLabel("Username").fill(username);
    await page.getByLabel(/Groups/i).fill(groups);
    // Role defaults to User. Don't click anything to keep it.
    await page.getByRole("button", { name: /Sign in/i }).click();

    // After login, ProtectedRoute redirects to /desktops.
    await expect(page).toHaveURL(/\/desktops$/);

    // The page heading is "Your desktops".
    await expect(
      page.getByRole("heading", { level: 1, name: "Your desktops" }),
    ).toBeVisible();

    // At least one pool card is rendered. Cards render as <article>
    // with the pool's display_name as the heading. We don't pin a
    // specific name because the test is parameterized on the
    // entitled pool.
    const cards = page.locator("article").filter({
      has: page.getByRole("link", { name: /Connect|Resume/i }),
    });
    await expect(cards).toHaveCount(1);

    // The launcher's username is visible in the AppShell header.
    await expect(page.getByText(username)).toBeVisible();

    // Logout returns to /login and the launcher route is no longer
    // reachable without re-authenticating. (AppShell logout button's
    // accessible name is "Log out".)
    await page.getByRole("button", { name: /Log out/i }).click();
    await expect(page).toHaveURL(/\/login$/);

    // Going back to /desktops directly should bounce to /login.
    await page.goto("/desktops");
    await expect(page).toHaveURL(/\/login/);
  });
});
