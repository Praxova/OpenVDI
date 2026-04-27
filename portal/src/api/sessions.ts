import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useBrokerClient } from "./client";
import { desktopsKeys } from "./desktops";
import type { UserSessionView } from "@/types";

/**
 * Query key factory for the /me/sessions surface.
 *
 *   sessionsKeys.all              — prefix (M3-06's invalidation
 *                                   target; matches every key below)
 *   sessionsKeys.list(included)   — concrete list query, keyed by the
 *                                   include_ended flag so active-only
 *                                   and all-sessions caches don't
 *                                   collide
 *
 * Per TanStack Query semantics, invalidating a prefix key invalidates
 * every key with that prefix — so M3-06's
 * `invalidateQueries({ queryKey: sessionsKeys.all })` correctly hits
 * both list variants.
 */
export const sessionsKeys = {
  all: ["me", "sessions"] as const,
  list: (includeEnded: boolean) =>
    ["me", "sessions", "list", { includeEnded }] as const,
};

/**
 * Fetch the user's sessions. Pass `includeEnded=true` to include
 * disconnected/errored/timed_out sessions; otherwise only active.
 *
 * The broker default is `include_ended=false`; we always pass the
 * flag explicitly so the URL is unambiguous in DevTools.
 */
export function useSessionsQuery(includeEnded: boolean) {
  const client = useBrokerClient();
  return useQuery({
    queryKey: sessionsKeys.list(includeEnded),
    queryFn: () =>
      client.get<UserSessionView[]>(
        `/api/v1/me/sessions?include_ended=${includeEnded}`,
      ),
  });
}

/**
 * Disconnect a session by id. The broker endpoint is idempotent on
 * already-ended sessions (returns 204 either way), so a "session
 * already gone" race produces a successful mutation rather than an
 * error — matching the M2-16 user.py docstring.
 *
 * On both success AND error we invalidate the launcher and sessions
 * caches. Even if the disconnect failed at the broker, we want the
 * launcher's next refetch to surface the actual current state — the
 * worst that happens is one stale render before TanStack Query's
 * background refetch corrects it.
 */
export function useDisconnectSessionMutation() {
  const client = useBrokerClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) =>
      client.delete<void>(`/api/v1/me/sessions/${sessionId}`),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: desktopsKeys.all });
      queryClient.invalidateQueries({ queryKey: sessionsKeys.all });
    },
  });
}
