import { createContext, useContext } from "react";

import type { APIResponse } from "@/types";
import { BrokerError } from "./errors";

/**
 * Function the auth context exposes to the BrokerClient. Returns the
 * headers to inject on every request. `{}` is a valid return value
 * (no auth) but BrokerClient does not assume anything about contents
 * — the caller decides what's authoritative.
 *
 * In M3, this returns `{ "X-Dev-User": ..., "X-Dev-Groups": ..., "X-Dev-Role": ... }`
 * for an authenticated dev session, or `{}` if unauthenticated.
 *
 * In M4, it returns `{ Authorization: "Bearer ..." }`. Same seam.
 */
export type AuthHeadersProvider = () => Record<string, string>;

export interface BrokerClientOptions {
  /**
   * Base URL for API requests. In dev: an empty string (so requests go
   * to `/api/v1/...` and Vite's proxy forwards). In prod: also empty
   * if the static build is served from the broker; an absolute URL if
   * the portal runs separately.
   */
  baseUrl: string;

  /** Producer for the per-request auth headers. */
  getAuthHeaders: AuthHeadersProvider;

  /**
   * Override for `fetch`. Defaults to the global. Tests inject a
   * `vi.fn()` to drive HTTP outcomes deterministically.
   */
  fetchImpl?: typeof fetch;
}

export class BrokerClient {
  private readonly baseUrl: string;
  private readonly getAuthHeaders: AuthHeadersProvider;
  private readonly fetchImpl: typeof fetch;

  constructor(opts: BrokerClientOptions) {
    this.baseUrl = opts.baseUrl;
    this.getAuthHeaders = opts.getAuthHeaders;
    this.fetchImpl = opts.fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  // ── Public verb-shaped helpers ──────────────────────────────

  async get<T>(path: string, init?: Omit<RequestInit, "method" | "body">): Promise<T> {
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
    const url = this.baseUrl + path;
    const headers: Record<string, string> = {
      Accept: "application/json",
      ...this.getAuthHeaders(),
      ...((init?.headers as Record<string, string> | undefined) ?? {}),
    };
    let serializedBody: string | undefined;
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      serializedBody = JSON.stringify(body);
    }

    let response: Response;
    try {
      response = await this.fetchImpl(url, {
        ...init,
        method,
        headers,
        body: serializedBody,
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

    // 204 No Content — no body. T must be void/undefined for callers
    // that expect this; we return undefined cast to T.
    if (response.status === 204) {
      return undefined as T;
    }

    // Try to parse the body as JSON. If parsing fails on a 2xx, that's
    // a broker bug — surface it as INTERNAL_ERROR. If parsing fails on
    // a non-2xx, fall through to the no-envelope error path below.
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
        // 2xx with `error != null` — the broker is misbehaving.
        // Treat it as INTERNAL_ERROR rather than silently returning
        // `null`-as-T. This protects pages from surprise null payloads.
        throw new BrokerError({
          httpStatus: response.status,
          code: env.error.code ?? "INTERNAL_ERROR",
          message: env.error.message ?? "broker returned 2xx with error envelope",
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

    // Non-2xx path — preferred shape is our envelope, but tolerate
    // anything (e.g. an upstream proxy returning HTML for a 502).
    if (!parseFailed && parsed && typeof parsed === "object" && "error" in parsed) {
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
    // Fallback: no envelope, or a corrupt one.
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
 * Context for the live `BrokerClient` instance. M3-03's
 * `BrokerClientProvider` constructs the instance (with the auth
 * callback wired in) and provides it; pages and queries read it via
 * `useBrokerClient()`.
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
        "M3-03 should have mounted the provider above this component.",
    );
  }
  return client;
}
