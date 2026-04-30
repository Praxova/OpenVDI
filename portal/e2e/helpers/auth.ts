import { type Page } from "@playwright/test";


export interface E2EEnv {
  brokerUrl: string;
  adminUser: string;
  adminPassword: string;
  testUser: string;
  testUserPassword: string;
  testClusterName: string;
  testTemplateName: string;
  testVmidStart: number;
}


export function readEnv(): E2EEnv {
  const required = (name: string) => {
    const v = process.env[name];
    if (v === undefined || v === "") {
      throw new Error(`${name} must be set for the M4 admin smoke test.`);
    }
    return v;
  };
  return {
    brokerUrl: process.env.OPENVDI_BROKER_URL ?? "http://localhost:8080",
    adminUser: required("OPENVDI_TEST_ADMIN_USER"),
    adminPassword: required("OPENVDI_TEST_ADMIN_PASSWORD"),
    testUser: required("OPENVDI_TEST_USER"),
    testUserPassword: required("OPENVDI_TEST_USER_PASSWORD"),
    testClusterName: process.env.OPENVDI_TEST_CLUSTER_NAME ?? "e2e-cluster",
    testTemplateName:
      process.env.OPENVDI_TEST_TEMPLATE_NAME ?? "e2e-template",
    testVmidStart: Number(process.env.OPENVDI_TEST_VMID_RANGE_START ?? "9000"),
  };
}


export async function loginAsAdmin(page: Page, env: E2EEnv): Promise<void> {
  await page.goto("/login");
  await page.getByLabel(/^Username/i).fill(env.adminUser);
  await page.getByLabel(/^Password/i).fill(env.adminPassword);
  await page.getByRole("button", { name: /^Sign in$/i }).click();
  // Post-login lands on /desktops or whatever the user originally
  // requested. Admin can land on either; allow both.
  await page.waitForURL(/\/(desktops|admin)/);
}


export async function loginAsUser(page: Page, env: E2EEnv): Promise<void> {
  await page.goto("/login");
  await page.getByLabel(/^Username/i).fill(env.testUser);
  await page.getByLabel(/^Password/i).fill(env.testUserPassword);
  await page.getByRole("button", { name: /^Sign in$/i }).click();
  await page.waitForURL(/\/desktops$/);
}


export async function logout(page: Page): Promise<void> {
  // The AppShell's logout button has aria-label "Log out".
  await page.getByRole("button", { name: /^Log out$/i }).click();
  await page.waitForURL(/\/login$/);
}


/**
 * Get a bearer access token for cleanup-via-API. Avoids driving the UI
 * for teardown — UI-driven teardown adds flake surface.
 *
 * Uses raw `fetch` (not Playwright's `request` context) because the
 * cleanup teardown sometimes runs after `page` is closed but before
 * the browser context is disposed; `fetch` is unconditional and
 * standalone.
 */
export async function getAdminToken(env: E2EEnv): Promise<string> {
  const response = await fetch(`${env.brokerUrl}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username: env.adminUser,
      password: env.adminPassword,
    }),
  });
  if (!response.ok) {
    throw new Error(`Admin login failed: ${response.status}`);
  }
  const body = (await response.json()) as {
    data: { access_token: string };
  };
  return body.data.access_token;
}
