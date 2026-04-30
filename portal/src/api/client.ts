import { createContext, useContext } from "react";

import type { APIResponse } from "@/types";
import { BrokerError } from "./errors";

export interface BrokerClientOptions {
  /**
   * Base URL for API requests. In dev: an empty string (so requests go
   * to `/api/v1/...` and Vite's proxy forwards). In prod: also empty
   * if the static build is served from the broker; an absolute URL if
   * the portal runs separately.
   */
  baseUrl: string;

  /** Returns the current access token, or null if not authenticated.
      Called on every outgoing non-auth request to inject the bearer
      header. */
  getAccessToken: () => string | null;

  /** Called when the broker returns 401 on a non-auth endpoint.
      Triggers a refresh; returns the new access token (or null if
      refresh failed). De-duplication is the caller's responsibility
      (AuthContext does this via its in-flight-promise ref). */
  onUnauthorized: () => Promise<string | null>;

  /**
   * Override for `fetch`. Defaults to the global. Tests inject a
   * `vi.fn()` to drive HTTP outcomes deterministically.
   */
  fetchImpl?: typeof fetch;
}

const AUTH_ENDPOINT_PREFIX = "/api/v1/auth/";

export class BrokerClient {
  private readonly baseUrl: string;
  private readonly getAccessToken: () => string | null;
  private readonly onUnauthorized: () => Promise<string | null>;
  private readonly fetchImpl: typeof fetch;

  constructor(opts: BrokerClientOptions) {
    this.baseUrl = opts.baseUrl;
    this.getAccessToken = opts.getAccessToken;
    this.onUnauthorized = opts.onUnauthorized;
    this.fetchImpl = opts.fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  // ── Public verb-shaped helpers ──────────────────────────────

  async get<T>(
    path: string,
    init?: Omit<RequestInit, "method" | "body">,
  ): Promise<T> {
    return this.request<T>("GET", path, undefined, init);
  }

  async post<T>(
    path: string,
    body?: unknown,
    init?: Omit<RequestInit, "method" | "body">,
  ): Promise<T> {
    return this.request<T>("POST", path, body, init);
  }

  async put<T>(
    path: string,
    body: unknown,
    init?: Omit<RequestInit, "method" | "body">,
  ): Promise<T> {
    return this.request<T>("PUT", path, body, init);
  }

  /**
   * DELETE returns void for 204 No Content (the common case — every
   * destructive M2 endpoint returns either 202 Accepted with an envelope
   * or 204 No Content with no body). For an envelope-bearing 2xx, the
   * unwrapped data is returned typed as `T`. Callers that don't care
   * about the response body can `await client.delete<void>(path)`.
   */
  async delete<T = void>(
    path: string,
    init?: Omit<RequestInit, "method" | "body">,
  ): Promise<T> {
    return this.request<T>("DELETE", path, undefined, init);
  }

  // ── Single shared request path ──────────────────────────────

  private async request<T>(
    method: string,
    path: string,
    body: unknown,
    init: Omit<RequestInit, "method" | "body"> | undefined,
  ): Promise<T> {
    const isAuthEndpoint = path.startsWith(AUTH_ENDPOINT_PREFIX);

    let response = await this.attempt(method, path, body, init, isAuthEndpoint);

    // On 401 from a non-auth endpoint, try a refresh + replay. One
    // attempt only — looping risks request → refresh → 401 → ...
    // in a broken-state cluster.
    if (response.status === 401 && !isAuthEndpoint) {
      const newToken = await this.onUnauthorized();
      if (newToken !== null) {
        response = await this.attempt(method, path, body, init, isAuthEndpoint);
      }
    }

    return this.handleResponse<T>(response);
  }

  private async attempt(
    method: string,
    path: string,
    body: unknown,
    init: Omit<RequestInit, "method" | "body"> | undefined,
    isAuthEndpoint: boolean,
  ): Promise<Response> {
    const url = this.baseUrl + path;
    const headers: Record<string, string> = {
      Accept: "application/json",
      ...((init?.headers as Record<string, string> | undefined) ?? {}),
    };
    if (!isAuthEndpoint) {
      const token = this.getAccessToken();
      if (token !== null) {
        headers["Authorization"] = `Bearer ${token}`;
      }
    }
    let serializedBody: string | undefined;
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      serializedBody = JSON.stringify(body);
    }

    try {
      return await this.fetchImpl(url, {
        ...init,
        method,
        headers,
        body: serializedBody,
        // Required for the refresh-cookie carry on auth endpoints,
        // and harmless on non-auth same-origin requests. Same-origin
        // per A10.
        credentials: "include",
      });
    } catch (e) {
      // Network unreachable, DNS failure, request aborted, etc.
      // Promote to a typed BrokerError so pages have a single error
      // type to handle.
      const message =
        e instanceof Error
          ? `network error: ${e.message}`
          : `network error: ${String(e)}`;
      throw BrokerError.transport(message);
    }
  }

  private async handleResponse<T>(response: Response): Promise<T> {
    // 204 No Content — no body. T must be void/undefined for callers
    // that expect this; we return undefined cast to T.
    if (response.status === 204) {
      return undefined as T;
    }

    let parsed: APIResponse<T> | unknown;
    let parseFailed = false;
    try {
      parsed = await response.json();
    } catch {
      parseFailed = true;
    }

    if (response.ok && !parseFailed) {
      const env = parsed as APIResponse<T>;
      if (env.error !== null) {
        throw new BrokerError({
          httpStatus: response.status,
          code: env.error.code ?? "INTERNAL_ERROR",
          message:
            env.error.message ?? "broker returned 2xx with error envelope",
          envelope: env.error,
        });
      }
      return env.data as T;
    }

    if (response.ok && parseFailed) {
      throw new BrokerError({
        httpStatus: response.status,
        code: "INTERNAL_ERROR",
        message: "broker returned non-JSON 2xx body",
        envelope: null,
      });
    }

    if (
      !parseFailed &&
      parsed &&
      typeof parsed === "object" &&
      "error" in parsed
    ) {
      const errEnv = (parsed as APIResponse<unknown>).error;
      if (errEnv !== null) {
        throw new BrokerError({
          httpStatus: response.status,
          code: errEnv.code,
          message: errEnv.message,
          envelope: errEnv,
        });
      }
    }
    throw new BrokerError({
      httpStatus: response.status,
      code: response.status >= 500 ? "INTERNAL_ERROR" : "ERROR",
      message: `HTTP ${response.status}`,
      envelope: null,
    });
  }
}

// ── React context binding ──────────────────────────────────────

/**
 * Context for the live `BrokerClient` instance. `BrokerClientProvider`
 * constructs the instance (with auth callbacks wired in) and provides
 * it; pages and queries read it via `useBrokerClient()`.
 *
 * The default value is `null` so a `useBrokerClient()` outside a
 * provider throws — early loud failure beats silent surprise.
 */
export const BrokerClientContext = createContext<BrokerClient | null>(null);

export function useBrokerClient(): BrokerClient {
  const client = useContext(BrokerClientContext);
  if (client === null) {
    throw new Error(
      "useBrokerClient called outside a <BrokerClientProvider>. " +
        "main.tsx mounts the provider above this component.",
    );
  }
  return client;
}
