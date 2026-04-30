import { BrokerClient } from "@/api/client";
import { BrokerError } from "@/api/errors";

// describe/it/expect/vi come from vitest globals (configured in
// vitest.config.ts and exposed to TypeScript via "vitest/globals" in
// tsconfig.json).

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface MakeClientOpts {
  fetchImpl: typeof fetch;
  getAccessToken?: () => string | null;
  onUnauthorized?: () => Promise<string | null>;
}

function makeClient(opts: MakeClientOpts): BrokerClient {
  return new BrokerClient({
    baseUrl: "",
    getAccessToken: opts.getAccessToken ?? (() => "tok123"),
    onUnauthorized: opts.onUnauthorized ?? (async () => null),
    fetchImpl: opts.fetchImpl,
  });
}

describe("BrokerClient", () => {
  // ── Envelope handling (carried from M3) ─────────────────────

  it("unwraps a successful 2xx envelope", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(jsonResponse({ data: { id: "abc" }, error: null }));
    const client = makeClient({ fetchImpl });
    const result = await client.get<{ id: string }>("/api/v1/foo");
    expect(result).toEqual({ id: "abc" });
  });

  it("returns undefined for 204 No Content", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 204 }));
    const client = makeClient({ fetchImpl });
    const result = await client.delete<void>("/api/v1/me/sessions/x");
    expect(result).toBeUndefined();
  });

  it("returns data when envelope has error=null", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(jsonResponse({ data: ["a", "b"], error: null }));
    const client = makeClient({ fetchImpl });
    const result = await client.get<string[]>("/api/v1/foo");
    expect(result).toEqual(["a", "b"]);
  });

  it("throws BrokerError if a 2xx response carries a non-null error", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      jsonResponse({
        data: null,
        error: { code: "INTERNAL_ERROR", message: "weird state" },
      }),
    );
    const client = makeClient({ fetchImpl });
    const caught = await client.get("/api/v1/foo").catch((e: unknown) => e);
    expect(caught).toBeInstanceOf(BrokerError);
    const err = caught as BrokerError;
    expect(err.httpStatus).toBe(200);
    expect(err.code).toBe("INTERNAL_ERROR");
  });

  it("throws BrokerError on 4xx with envelope", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      jsonResponse(
        { data: null, error: { code: "FORBIDDEN", message: "no" } },
        403,
      ),
    );
    const client = makeClient({ fetchImpl });
    await expect(client.get("/api/v1/foo")).rejects.toMatchObject({
      name: "BrokerError",
      httpStatus: 403,
      code: "FORBIDDEN",
      message: "no",
    });
  });

  it("throws BrokerError on 5xx with envelope", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      jsonResponse(
        {
          data: null,
          error: {
            code: "PROVIDER_ERROR",
            message: "proxmox unreachable",
            details: { provider: "proxmox" },
          },
        },
        502,
      ),
    );
    const client = makeClient({ fetchImpl });
    const caught = await client.get("/api/v1/foo").catch((e: unknown) => e);
    expect(caught).toBeInstanceOf(BrokerError);
    const err = caught as BrokerError;
    expect(err.httpStatus).toBe(502);
    expect(err.code).toBe("PROVIDER_ERROR");
  });

  it("throws BrokerError with INTERNAL_ERROR when 5xx response has no envelope", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response("<!doctype html><html>Bad Gateway</html>", {
        status: 502,
        headers: { "Content-Type": "text/html" },
      }),
    );
    const client = makeClient({ fetchImpl });
    const caught = await client.get("/api/v1/foo").catch((e: unknown) => e);
    expect(caught).toBeInstanceOf(BrokerError);
    const err = caught as BrokerError;
    expect(err.httpStatus).toBe(502);
    expect(err.code).toBe("INTERNAL_ERROR");
  });

  it("promotes a transport failure to BrokerError with httpStatus=0", async () => {
    const fetchImpl = vi
      .fn()
      .mockRejectedValue(new TypeError("Failed to fetch"));
    const client = makeClient({ fetchImpl });
    const caught = await client.get("/api/v1/foo").catch((e: unknown) => e);
    expect(caught).toBeInstanceOf(BrokerError);
    const err = caught as BrokerError;
    expect(err.httpStatus).toBe(0);
    expect(err.code).toBe("INTERNAL_ERROR");
    expect(err.message).toContain("network error");
  });

  it("serializes a POST body as JSON and sets Content-Type", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ data: { ok: true }, error: null }, 201),
      );
    const client = makeClient({ fetchImpl });
    await client.post("/api/v1/foo", { name: "alice" });
    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/v1/foo",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ name: "alice" }),
        headers: expect.objectContaining({
          "Content-Type": "application/json",
        }),
      }),
    );
  });

  it("does not set Content-Type when there is no body", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(jsonResponse({ data: null, error: null }));
    const client = makeClient({ fetchImpl });
    await client.get("/api/v1/foo");
    const callArgs = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(callArgs).toBeDefined();
    const init = callArgs![1] as RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBeUndefined();
    expect(init.body).toBeUndefined();
  });

  // ── Bearer header injection ─────────────────────────────────

  it("attaches Authorization: Bearer header when access token set", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(jsonResponse({ data: {}, error: null }));
    const client = makeClient({
      fetchImpl,
      getAccessToken: () => "tok123",
    });
    await client.get("/api/v1/foo");
    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/v1/foo",
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer tok123",
        }),
      }),
    );
  });

  it("omits Authorization header when access token is null", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(jsonResponse({ data: {}, error: null }));
    const client = makeClient({ fetchImpl, getAccessToken: () => null });
    await client.get("/api/v1/foo");
    const callArgs = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0];
    const init = callArgs![1] as RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(headers["Authorization"]).toBeUndefined();
  });

  it("does NOT attach bearer for /api/v1/auth/* endpoints", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(jsonResponse({ data: {}, error: null }));
    const client = makeClient({
      fetchImpl,
      getAccessToken: () => "tok123",
    });
    await client.post("/api/v1/auth/login", { username: "x", password: "y" });
    const init = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0]![1] as
      RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(headers["Authorization"]).toBeUndefined();
  });

  it("includes credentials: 'include' on every request", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(jsonResponse({ data: {}, error: null }));
    const client = makeClient({ fetchImpl });
    await client.get("/api/v1/foo");
    const init = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0]![1] as
      RequestInit;
    expect(init.credentials).toBe("include");
  });

  // ── 401-refresh-replay ──────────────────────────────────────

  it("calls onUnauthorized on 401 from non-auth endpoint", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ data: null, error: { code: "UNAUTHORIZED" } }, 401),
      )
      .mockResolvedValueOnce(jsonResponse({ data: { ok: true }, error: null }));
    const onUnauthorized = vi.fn().mockResolvedValue("newtok");
    const client = makeClient({
      fetchImpl,
      getAccessToken: () => "oldtok",
      onUnauthorized,
    });
    await client.get("/api/v1/foo");
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("does NOT call onUnauthorized for /api/v1/auth/* 401s", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ data: null, error: { code: "UNAUTHORIZED" } }, 401),
      );
    const onUnauthorized = vi.fn();
    const client = makeClient({
      fetchImpl,
      getAccessToken: () => "tok",
      onUnauthorized,
    });
    await client
      .post("/api/v1/auth/login", { username: "x", password: "y" })
      .catch(() => {});
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("replays original request with new token after refresh", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ data: null, error: { code: "UNAUTHORIZED" } }, 401),
      )
      .mockResolvedValueOnce(jsonResponse({ data: { ok: true }, error: null }));
    let currentToken = "oldtok";
    const onUnauthorized = vi.fn().mockImplementation(async () => {
      currentToken = "newtok";
      return currentToken;
    });
    const client = makeClient({
      fetchImpl,
      getAccessToken: () => currentToken,
      onUnauthorized,
    });
    const result = await client.get<{ ok: boolean }>("/api/v1/foo");
    expect(result).toEqual({ ok: true });

    // First call: oldtok. Second call (replay): newtok.
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    const firstHeaders = (fetchImpl.mock.calls[0]![1] as RequestInit)
      .headers as Record<string, string>;
    const secondHeaders = (fetchImpl.mock.calls[1]![1] as RequestInit)
      .headers as Record<string, string>;
    expect(firstHeaders["Authorization"]).toBe("Bearer oldtok");
    expect(secondHeaders["Authorization"]).toBe("Bearer newtok");
  });

  it("returns 401 BrokerError if refresh failed", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ data: null, error: { code: "UNAUTHORIZED" } }, 401),
      );
    const onUnauthorized = vi.fn().mockResolvedValue(null);
    const client = makeClient({
      fetchImpl,
      getAccessToken: () => "oldtok",
      onUnauthorized,
    });
    await expect(client.get("/api/v1/foo")).rejects.toMatchObject({
      name: "BrokerError",
      httpStatus: 401,
    });
    // Original call only — no replay.
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("does not retry more than once on persistent 401", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ data: null, error: { code: "UNAUTHORIZED" } }, 401),
      );
    const onUnauthorized = vi.fn().mockResolvedValue("newtok");
    const client = makeClient({
      fetchImpl,
      getAccessToken: () => "tok",
      onUnauthorized,
    });
    await expect(client.get("/api/v1/foo")).rejects.toMatchObject({
      name: "BrokerError",
      httpStatus: 401,
    });
    // Original + one replay = 2 fetches; no third attempt.
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });
});
