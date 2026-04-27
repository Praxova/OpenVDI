import type { APIErrorCode, APIErrorEnvelope } from "@/types";

/**
 * Thrown by `BrokerClient` for every non-2xx response from the broker
 * AND for transport-level failures (network unreachable, JSON parse
 * failure on a response that should have been JSON).
 *
 * Pages MUST dispatch on `code`, not `httpStatus`. Two different 503s
 * — POOL_FULL and SERVICE_UNAVAILABLE — share an HTTP status and have
 * different remediation. Same with the 502/504 PROVIDER_* family.
 *
 * The original envelope (when one was present) is preserved on
 * `envelope` for debugging; pages should not pattern-match on it.
 */
export class BrokerError extends Error {
  readonly httpStatus: number;
  readonly code: APIErrorCode;
  readonly envelope: APIErrorEnvelope | null;

  constructor(opts: {
    httpStatus: number;
    code: APIErrorCode;
    message: string;
    envelope: APIErrorEnvelope | null;
  }) {
    super(opts.message);
    // Restore prototype chain — required so `instanceof BrokerError`
    // works through transpilation when targeting older runtimes. Vite
    // builds for ES2022 here so this is belt-and-suspenders, but the
    // failure mode is silent and the cost is one line.
    Object.setPrototypeOf(this, BrokerError.prototype);
    this.name = "BrokerError";
    this.httpStatus = opts.httpStatus;
    this.code = opts.code;
    this.envelope = opts.envelope;
  }

  /**
   * Convenience for the common case: a transport-level failure (network
   * unreachable, DNS failure, request aborted). httpStatus=0 is the
   * pinned convention; pages can pattern-match `err.httpStatus === 0`
   * for "is this a network problem?".
   */
  static transport(message: string): BrokerError {
    return new BrokerError({
      httpStatus: 0,
      code: "INTERNAL_ERROR",
      message,
      envelope: null,
    });
  }
}
