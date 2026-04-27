import { useQuery } from "@tanstack/react-query";

import { useBrokerClient } from "./client";
import type { UserPoolView } from "@/types";

/**
 * Query keys for the desktops surface. Centralized so M3-06's connect
 * mutation and M3-07's sessions view (which may also want to
 * invalidate) reference the same array shape rather than each
 * inventing their own.
 *
 * Per FE5 in the M3 seed: keys mirror REST paths. ['me','desktops']
 * scopes to the current user's view; the singular ['me'] root is
 * reserved for future per-user metadata if it ever lands.
 */
export const desktopsKeys = {
  all: ["me", "desktops"] as const,
};

/**
 * Fetch the entitled-pool list for the currently logged-in user.
 *
 * Returns `UserPoolView[]`. Empty array means "no entitlements" —
 * not an error; the launcher renders the empty state.
 *
 * Errors surface as `BrokerError` (the global TanStack Register
 * defaultError declared in lib/queryClient.ts). The launcher's error
 * UI dispatches on `error.code` rather than HTTP status.
 */
export function useDesktopsQuery() {
  const client = useBrokerClient();
  return useQuery({
    queryKey: desktopsKeys.all,
    queryFn: () => client.get<UserPoolView[]>("/api/v1/me/desktops"),
  });
}
