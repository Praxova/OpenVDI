import { useMutation, useQueryClient } from "@tanstack/react-query";

import { useBrokerClient } from "./client";
import { desktopsKeys } from "./desktops";

/**
 * Query key factory for the /me/sessions surface. M3-07's
 * useSessionsQuery() will add to this same factory; the disconnect
 * mutation here invalidates `sessionsKeys.all` so a session
 * disconnect from the console is immediately reflected on the
 * sessions view next time the user navigates there.
 */
export const sessionsKeys = {
  all: ["me", "sessions"] as const,
};

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
