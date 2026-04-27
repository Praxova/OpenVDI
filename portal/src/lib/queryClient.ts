import { QueryClient } from "@tanstack/react-query";

import { BrokerError } from "@/api/errors";

// Global error type for every TanStack Query hook in this app. Without
// this declaration, queries default to `error: Error | null` and pages
// have to `instanceof BrokerError`-narrow on every error access. With
// it, useQuery / useMutation infer `error: BrokerError | null`
// automatically — no per-call type parameter required.
//
// `import type` keeps this from creating a runtime cycle between
// queryClient.ts and errors.ts; the declare module block below is
// purely compile-time.
declare module "@tanstack/react-query" {
  interface Register {
    defaultError: BrokerError;
  }
}

/**
 * Shared TanStack Query client.
 *
 * Tuning notes (M3 baseline; revisit in M4 with real load):
 *   - staleTime 30s: launcher and sessions views feel responsive without
 *     hammering the broker. Mutations invalidate explicitly so changes
 *     appear immediately.
 *   - retry policy: one retry on transient failures (httpStatus >= 500
 *     or transport errors), zero retries on 4xx — those are
 *     deterministic and retrying spams the user with the same error.
 *   - refetchOnWindowFocus: false. The console route in M3-06 holds an
 *     active VNC session; refetching the desktops list every time the
 *     user alt-tabs is more noise than signal. Re-enable per-query if
 *     a dashboard view in M4 wants it.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        if (failureCount >= 1) return false;
        if (error instanceof BrokerError) {
          // Retry on server-side or transport-level failures.
          return error.httpStatus === 0 || error.httpStatus >= 500;
        }
        // Unknown error type — be conservative, don't retry.
        return false;
      },
      // Default retry delay (1s, 2s, 4s, ...). One retry → effectively 1s.
    },
    mutations: {
      // Mutations are user-initiated (Connect button, Disconnect button).
      // No automatic retry — the user can re-click if a transient failure
      // surfaces. Auto-retry on a connect mutation could double-broker a
      // desktop in edge cases, even with the per-user-per-pool advisory
      // lock M2-08 added.
      retry: false,
    },
  },
});
