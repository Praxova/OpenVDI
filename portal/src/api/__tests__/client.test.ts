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

function makeClient(fetchImpl: typeof fetch): BrokerClient {
  return new BrokerClient({
    baseUrl: "",
    getAuthHeaders: () => ({ "X-Dev-User": "alice" }),
    fetchImpl,
  });
}

describe("BrokerClient", () => {
  // 1. GET success
  it("unwraps a successful 2xx envelope", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(jsonResponse({ data: { id: "abc" }, error: null }));
    const client = makeClient(fetchImpl);
    const result = await client.get<{ id: string }>("/api/v1/foo");
    expect(result).toEqual({ id: "abc" });
  });

  // 2. 204 No Content
  it("returns undefined for 204 No Content", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 204 }));
    const client = makeClient(fetchImpl);
    const result = await client.delete<void>("/api/v1/me/sessions/x");
    expect(result).toBeUndefined();
  });

  // 3. 2xx with `error: null` and `data: T` — happy path with explicit null
  it("returns data when envelope has error=null", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(jsonResponse({ data: ["a", "b"], error: null }));
    const client = makeClient(fetchImpl);
    const result = await client.get<string[]>("/api/v1/foo");
    expect(result).toEqual(["a", "b"]);
  });

  // 4. 2xx with `error: <envelope>` — broker bug guard
  it("throws BrokerError if a 2xx response carries a non-null error", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      jsonResponse({
        data: null,
        error: { code: "INTERNAL_ERROR", message: "weird state" },
      }),
    );
    const client = makeClient(fetchImpl);
    const caught = await client.get("/api/v1/foo").catch((e: unknown) => e);
    expect(caught).toBeInstanceOf(BrokerError);
    const err = caught as BrokerError;
    expect(err.httpStatus).toBe(200);
    expect(err.code).toBe("INTERNAL_ERROR");
    expect(err.envelope).toMatchObject({ code: "INTERNAL_ERROR" });
  });

  // 5. 4xx with envelope
  it("throws BrokerError on 4xx with envelope", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      jsonResponse(
        { data: null, error: { code: "FORBIDDEN", message: "no" } },
        403,
      ),
    );
    const client = makeClient(fetchImpl);
    await expect(client.get("/api/v1/foo")).rejects.toMatchObject({
      name: "BrokerError",
      httpStatus: 403,
      code: "FORBIDDEN",
      message: "no",
    });
  });

  // 6. 5xx with envelope
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
    const client = makeClient(fetchImpl);
    const caught = await client.get("/api/v1/foo").catch((e: unknown) => e);
    expect(caught).toBeInstanceOf(BrokerError);
    const err = caught as BrokerError;
    expect(err.httpStatus).toBe(502);
    expect(err.code).toBe("PROVIDER_ERROR");
    expect(err.envelope?.details).toEqual({ provider: "proxmox" });
  });

  // 7. 5xx without envelope (e.g. proxy returned HTML)
  it("throws BrokerError with INTERNAL_ERROR when 5xx response has no envelope", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response("<!doctype html><html>Bad Gateway</html>", {
        status: 502,
        headers: { "Content-Type": "text/html" },
      }),
    );
    const client = makeClient(fetchImpl);
    const caught = await client.get("/api/v1/foo").catch((e: unknown) => e);
    expect(caught).toBeInstanceOf(BrokerError);
    const err = caught as BrokerError;
    expect(err.httpStatus).toBe(502);
    expect(err.code).toBe("INTERNAL_ERROR");
    expect(err.envelope).toBeNull();
  });

  // 8. Transport failure
  it("promotes a transport failure to BrokerError with httpStatus=0", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    const client = makeClient(fetchImpl);
    const caught = await client.get("/api/v1/foo").catch((e: unknown) => e);
    expect(caught).toBeInstanceOf(BrokerError);
    const err = caught as BrokerError;
    expect(err.httpStatus).toBe(0);
    expect(err.code).toBe("INTERNAL_ERROR");
    expect(err.message).toContain("network error");
  });

  // 9. Auth headers merged on every request
  it("merges auth headers on every request", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(jsonResponse({ data: {}, error: null }));
    const client = makeClient(fetchImpl);
    await client.get("/api/v1/foo");
    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/v1/foo",
      expect.objectContaining({
        headers: expect.objectContaining({ "X-Dev-User": "alice" }),
      }),
    );
  });

  // 10. Body serialization on POST
  it("serializes a POST body as JSON and sets Content-Type", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(jsonResponse({ data: { ok: true }, error: null }, 201));
    const client = makeClient(fetchImpl);
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

  // 11. No body for GET → no Content-Type
  it("does not set Content-Type when there is no body", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(jsonResponse({ data: null, error: null }));
    const client = makeClient(fetchImpl);
    await client.get("/api/v1/foo");
    const callArgs = (fetchImpl as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(callArgs).toBeDefined();
    const init = callArgs![1] as RequestInit;
    const headers = init.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBeUndefined();
    expect(init.body).toBeUndefined();
  });
});
