import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider, LoginError, useAuth } from "@/auth/AuthContext";

// ── Helpers ───────────────────────────────────────────────────

function makeJwt(claims: object): string {
  const header = btoa(JSON.stringify({ alg: "HS256", typ: "JWT" }))
    .replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
  const payload = btoa(JSON.stringify(claims))
    .replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
  return `${header}.${payload}.fakesig`;
}

function tokenResponse(opts?: {
  username?: string;
  groups?: string[];
  role?: "admin" | "user";
  expiresIn?: number;
}) {
  const claims = {
    sub: opts?.username ?? "alice",
    groups: opts?.groups ?? ["Engineering"],
    role: opts?.role ?? ("user" as const),
    iat: 0,
    exp: 9999999999,
    jti: "00000000-0000-0000-0000-000000000001",
  };
  return {
    data: {
      access_token: makeJwt(claims),
      expires_in: opts?.expiresIn ?? 900,
      role: claims.role,
    },
    error: null,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function wrapper({ children }: { children: ReactNode }) {
  return <AuthProvider>{children}</AuthProvider>;
}

// ── Tests ─────────────────────────────────────────────────────

describe("AuthContext", () => {
  let fetchSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("starts in 'initializing' state", () => {
    // Pending forever → mount-time refresh doesn't resolve.
    fetchSpy.mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useAuth(), { wrapper });
    expect(result.current.state.status).toBe("initializing");
  });

  it("transitions to 'authenticated' on successful initial refresh", async () => {
    fetchSpy.mockResolvedValueOnce(jsonResponse(tokenResponse()));
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() =>
      expect(result.current.state.status).toBe("authenticated"),
    );
    if (result.current.state.status !== "authenticated") {
      throw new Error("not authenticated");
    }
    expect(result.current.state.user).toEqual({
      username: "alice",
      groups: ["Engineering"],
      role: "user",
    });
    expect(result.current.getAccessToken()).not.toBeNull();
  });

  it("transitions to 'unauthenticated' on failed initial refresh", async () => {
    fetchSpy.mockResolvedValueOnce(
      jsonResponse({ data: null, error: { code: "UNAUTHORIZED" } }, 401),
    );
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() =>
      expect(result.current.state.status).toBe("unauthenticated"),
    );
  });

  it("login() POSTs to /auth/login with credentials: 'include'", async () => {
    // Initial refresh fails → unauthenticated.
    fetchSpy.mockResolvedValueOnce(
      jsonResponse({ data: null, error: { code: "UNAUTHORIZED" } }, 401),
    );
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() =>
      expect(result.current.state.status).toBe("unauthenticated"),
    );

    fetchSpy.mockResolvedValueOnce(jsonResponse(tokenResponse()));
    await act(async () => {
      await result.current.login("alice", "pw");
    });

    const loginCall = fetchSpy.mock.calls.find(
      (c) => (c[0] as string) === "/api/v1/auth/login",
    );
    expect(loginCall).toBeDefined();
    const init = loginCall![1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(init.body).toBe(JSON.stringify({ username: "alice", password: "pw" }));
    expect(result.current.state.status).toBe("authenticated");
  });

  it("login() throws LoginError on bad credentials", async () => {
    fetchSpy.mockResolvedValueOnce(
      jsonResponse({ data: null, error: { code: "UNAUTHORIZED" } }, 401),
    );
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() =>
      expect(result.current.state.status).toBe("unauthenticated"),
    );

    fetchSpy.mockResolvedValueOnce(
      jsonResponse(
        { data: null, error: { code: "UNAUTHORIZED", message: "nope" } },
        401,
      ),
    );
    let caught: unknown = null;
    await act(async () => {
      try {
        await result.current.login("alice", "wrong");
      } catch (e) {
        caught = e;
      }
    });
    expect(caught).toBeInstanceOf(LoginError);
    expect((caught as LoginError).code).toBe("UNAUTHORIZED");
  });

  it("refresh() de-duplicates concurrent calls", async () => {
    // Initial refresh resolves immediately.
    fetchSpy.mockResolvedValueOnce(jsonResponse(tokenResponse()));
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() =>
      expect(result.current.state.status).toBe("authenticated"),
    );

    const refreshCount = () =>
      fetchSpy.mock.calls.filter(
        (c) => (c[0] as string) === "/api/v1/auth/refresh",
      ).length;
    const before = refreshCount(); // 1 — the mount-effect refresh

    // Hold the next refresh open so 3 callers race against the same
    // in-flight promise.
    let resolve!: (r: Response) => void;
    const blocked = new Promise<Response>((r) => (resolve = r));
    fetchSpy.mockReturnValueOnce(blocked);

    const p1 = result.current.refresh();
    const p2 = result.current.refresh();
    const p3 = result.current.refresh();

    // All three calls share the SAME promise — only one new fetch.
    expect(refreshCount() - before).toBe(1);

    await act(async () => {
      resolve(jsonResponse(tokenResponse()));
      await Promise.all([p1, p2, p3]);
    });

    // Still only one new /auth/refresh after resolution.
    expect(refreshCount() - before).toBe(1);
  });

  it("logout() POSTs to /auth/logout and clears state", async () => {
    fetchSpy.mockResolvedValueOnce(jsonResponse(tokenResponse()));
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() =>
      expect(result.current.state.status).toBe("authenticated"),
    );

    fetchSpy.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await act(async () => {
      await result.current.logout();
    });

    const logoutCall = fetchSpy.mock.calls.find(
      (c) => (c[0] as string) === "/api/v1/auth/logout",
    );
    expect(logoutCall).toBeDefined();
    expect((logoutCall![1] as RequestInit).credentials).toBe("include");
    expect(result.current.state.status).toBe("unauthenticated");
  });

  it("schedules pre-emptive refresh ~60s before expiry", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    // Initial refresh: 120s lifetime → scheduler fires at 60s.
    fetchSpy.mockResolvedValueOnce(jsonResponse(tokenResponse({ expiresIn: 120 })));
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() =>
      expect(result.current.state.status).toBe("authenticated"),
    );

    const refreshCount = () =>
      fetchSpy.mock.calls.filter(
        (c) => (c[0] as string) === "/api/v1/auth/refresh",
      ).length;
    expect(refreshCount()).toBe(1); // mount refresh only

    // Queue the scheduled-refresh response.
    fetchSpy.mockResolvedValueOnce(jsonResponse(tokenResponse({ expiresIn: 120 })));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(refreshCount()).toBeGreaterThanOrEqual(2);
  });
});
