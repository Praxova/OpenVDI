/**
 * The broker's universal response envelope. Every JSON response from the
 * broker (except 204 No Content) takes this shape. M2-11's exception
 * handler family enforces it.
 *
 * The portal NEVER unwraps this manually inside a page. `BrokerClient`
 * unwraps once, at the HTTP boundary, and pages see the bare `T` (or a
 * thrown `BrokerError`).
 */
export type APIResponse<T> =
  | { data: T; error: null }
  | { data: null; error: APIErrorEnvelope };

export interface APIErrorEnvelope {
  code: APIErrorCode;
  message: string;
  /**
   * Admin-only diagnostic context. Populated for callers with
   * `role=admin` per `api-design.md` § Error Response Shape; omitted
   * (undefined) for regular users. The portal renders this only in
   * admin views (M4); for M3 it's surfaced verbatim in dev tooling
   * but not shown to end-users.
   */
  details?: Record<string, unknown>;
}

/**
 * The error code vocabulary M2-11 emits. Adding a code here without a
 * matching broker change is a bug; adding a code on the broker side
 * without updating this union is also a bug — pages would handle it as
 * `unknown` and probably miss it.
 *
 * Keep this list in lockstep with `broker/app/main.py` and the table
 * in `docs/api-design.md` → *Error Codes*.
 */
export type APIErrorCode =
  | "INVALID_REQUEST"
  | "UNAUTHORIZED"
  | "FORBIDDEN"
  | "NOT_FOUND"
  | "CONFLICT"
  | "POOL_FULL"
  | "PROVIDER_ERROR"
  | "PROVIDER_TIMEOUT"
  | "SERVICE_UNAVAILABLE"
  | "INTERNAL_ERROR"
  | "ERROR"; // generic fallback that M2-11's status-to-code map emits for
             // bare HTTP exceptions whose status doesn't appear in its table.
