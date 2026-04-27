import { request, type FullConfig } from "@playwright/test";

interface RequiredEnv {
  brokerUrl: string;
  testUser: string;
  testGroups: string;
  testPoolId: string;
  testAdminUser: string | null;
  testAdminGroups: string;
}

function readEnv(): RequiredEnv {
  const brokerUrl = process.env.OPENVDI_BROKER_URL ?? "http://localhost:8080";
  const testUser = process.env.OPENVDI_TEST_USER ?? "alice";
  const testGroups = process.env.OPENVDI_TEST_GROUPS ?? "";
  const testPoolId = process.env.OPENVDI_TEST_POOL_ID ?? "";
  const testAdminUser = process.env.OPENVDI_TEST_ADMIN_USER ?? null;
  const testAdminGroups = process.env.OPENVDI_TEST_ADMIN_GROUPS ?? "Admins";

  if (testPoolId === "") {
    throw new Error(
      "OPENVDI_TEST_POOL_ID is required. Set it to the UUID of a pool the test user is entitled to.",
    );
  }
  return {
    brokerUrl,
    testUser,
    testGroups,
    testPoolId,
    testAdminUser,
    testAdminGroups,
  };
}

/**
 * 1) Health-check the broker.
 * 2) If admin creds are provided, fire POST /pools/{id}/provision
 *    and wait up to 90s for at least one warm spare. The connect
 *    test then immediately consumes that spare.
 *
 * Both steps fail loudly — if the broker isn't reachable or the
 * pool can't produce a spare, the smoke test would fail in a more
 * confusing way later. Failing in setup is the kind feedback.
 */
async function globalSetup(_config: FullConfig): Promise<void> {
  const env = readEnv();
  const ctx = await request.newContext({ baseURL: env.brokerUrl });

  // ── Health check ────────────────────────────────────────────
  const health = await ctx.get("/health");
  if (!health.ok()) {
    throw new Error(
      `Broker /health returned ${health.status()}. Is the broker running on ${env.brokerUrl}?`,
    );
  }

  // ── Best-effort provision ──────────────────────────────────
  if (env.testAdminUser !== null) {
    const adminHeaders = {
      "X-Dev-User": env.testAdminUser,
      "X-Dev-Groups": env.testAdminGroups,
      "X-Dev-Role": "admin",
    };
    const provision = await ctx.post(
      `/api/v1/pools/${env.testPoolId}/provision`,
      { headers: adminHeaders },
    );
    // 200 (already at min_spare), 202 (kicked off), or 409 (already
    // provisioning) are all acceptable. Anything else is a problem.
    if (![200, 202, 409].includes(provision.status())) {
      throw new Error(
        `Provision returned ${provision.status()}: ${await provision.text()}`,
      );
    }

    // Poll up to 90s for at least one available desktop in the pool.
    // The /api/v1/pools/{id} response includes capacity stats per
    // M2's pool detail schema.
    const start = Date.now();
    let warmReady = false;
    while (Date.now() - start < 90_000) {
      const detail = await ctx.get(`/api/v1/pools/${env.testPoolId}`, {
        headers: adminHeaders,
      });
      if (detail.ok()) {
        const body = (await detail.json()) as {
          data: { available_count: number };
        };
        if (body.data.available_count >= 1) {
          warmReady = true;
          break;
        }
      }
      await new Promise((r) => setTimeout(r, 3000));
    }
    if (!warmReady) {
      console.warn(
        "Pool did not reach available_count>=1 within 90s. " +
          "Connect-flow.spec may fail with POOL_FULL.",
      );
    }
  }

  await ctx.dispose();
}

export default globalSetup;
