import { useMemo, type ReactNode } from "react";

import { BrokerClient, BrokerClientContext } from "./client";
import { useAuth } from "@/auth/AuthContext";

interface BrokerClientProviderProps {
  children: ReactNode;
}

/**
 * Constructs and provides the singleton `BrokerClient` for the app.
 *
 * Wires AuthContext's stable callbacks (`getAccessToken`, `refresh`)
 * into the client. Both callbacks are memoized with empty deps in
 * AuthContext (they read state via refs), so the BrokerClient itself
 * only needs to be constructed once. Re-creating it on every auth
 * state change would invalidate every TanStack Query's captured
 * `queryFn` reference and cause spurious re-fetches.
 */
export function BrokerClientProvider({ children }: BrokerClientProviderProps) {
  const { getAccessToken, refresh } = useAuth();

  const client = useMemo(() => {
    return new BrokerClient({
      // Empty baseUrl: requests go to `/api/v1/...` and Vite's dev
      // proxy forwards them. In production the static build is served
      // by the broker, also same-origin (per docs/deploy.md).
      baseUrl: "",
      getAccessToken,
      onUnauthorized: refresh,
    });
    // Empty deps — see JSDoc above. Both callbacks from useAuth are
    // stable; the lint rule wants them in deps but that defeats the
    // pattern.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <BrokerClientContext.Provider value={client}>
      {children}
    </BrokerClientContext.Provider>
  );
}
