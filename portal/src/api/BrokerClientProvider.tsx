import { useMemo, useRef, type ReactNode } from "react";

import { BrokerClient, BrokerClientContext } from "./client";
import { useAuth } from "@/auth/AuthContext";
import type { DevUser } from "@/auth/types";

interface BrokerClientProviderProps {
  children: ReactNode;
}

/**
 * Constructs and provides the singleton `BrokerClient` for the app.
 *
 * Construction strategy: single instance (memo'd with empty deps),
 * auth headers read fresh from a ref. Re-creating the client on every
 * auth state change would invalidate every TanStack Query's captured
 * `queryFn` reference and cause spurious re-fetches — worse, a
 * request in flight at logout could resolve after the client was
 * disposed, leading to confusing race conditions.
 *
 * The ref pattern (auth state mirrored into a ref via assignment) is
 * the standard React idiom for "I want the latest value, but I don't
 * want this to retrigger creation."
 */
export function BrokerClientProvider({ children }: BrokerClientProviderProps) {
  const { currentUser } = useAuth();
  const userRef = useRef<DevUser | null>(currentUser);
  userRef.current = currentUser;

  const client = useMemo(() => {
    return new BrokerClient({
      // Empty baseUrl: requests go to `/api/v1/...` and Vite's dev
      // proxy forwards them. In production the static build is served
      // by the broker, also same-origin. If the portal ever moves to
      // a separate host, M4 wires this through env.
      baseUrl: "",
      getAuthHeaders: (): Record<string, string> => {
        const u = userRef.current;
        if (u === null) return {};
        return {
          "X-Dev-User": u.username,
          "X-Dev-Groups": u.groups.join(","),
          "X-Dev-Role": u.role,
        };
      },
    });
    // The ref pattern requires empty deps — see the JSDoc above. The
    // lint rule wants `userRef` in the deps list, but that defeats
    // the entire pattern. Linters chase common bugs; this is the
    // rare deliberate exception.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <BrokerClientContext.Provider value={client}>
      {children}
    </BrokerClientContext.Provider>
  );
}
