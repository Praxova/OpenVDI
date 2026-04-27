import { defineConfig, devices } from "@playwright/test";

/**
 * OpenVDI portal end-to-end test config.
 *
 * Tests run against the local Vite dev server (auto-started via
 * `webServer`). The broker must be running externally on
 * OPENVDI_BROKER_URL (default http://localhost:8080) with a
 * configured cluster, the test pool, and an entitlement for the
 * test user.
 *
 * See portal/README.md → "Running the smoke test" for the full
 * setup checklist.
 */
export default defineConfig({
  testDir: "./e2e",
  // One pool, one warm spare → tests must serialize.
  fullyParallel: false,
  workers: 1,
  // Per-test timeout. Connect can take ~10-15s on a cold pool;
  // 60s gives generous headroom without hiding genuine hangs.
  timeout: 60_000,
  expect: {
    timeout: 10_000,
  },
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "e2e-report" }],
  ],

  // Each spec gets a fresh page. globalSetup runs once before
  // anything else.
  globalSetup: "./e2e/global-setup.ts",

  use: {
    baseURL: process.env.OPENVDI_PORTAL_URL ?? "http://localhost:5173",
    // PVE's vncwebsocket is wss:// to a self-signed host. Without
    // this the WebSocket upgrade fails silently and the canvas
    // never paints.
    ignoreHTTPSErrors: true,
    // Capture screenshots/videos on failure for triage.
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    trace: "on-first-retry",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  webServer: {
    command: "pnpm dev",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    stdout: "pipe",
    stderr: "pipe",
  },
});
