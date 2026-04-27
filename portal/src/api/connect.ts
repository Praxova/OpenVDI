import { useMutation, useQueryClient } from "@tanstack/react-query";

import { useBrokerClient } from "./client";
import { desktopsKeys } from "./desktops";
import type { ConnectResponse } from "@/types";

/**
 * Connect to a pool. Returns the broker's ConnectResponse on success
 * (session_id, desktop_name, ticket).
 *
 * The mutation invalidates the desktops launcher cache on success
 * so the user returning to /desktops sees the fresh `assigned_desktop`
 * field — without this, the launcher would render stale data for up
 * to the staleTime window (30s by default).
 *
 * Mutations are NOT auto-retried on transient failures (per the
 * mutation-default in lib/queryClient.ts). The user can re-trigger
 * via the page's "Try again" button. This is deliberate: the
 * per-user-per-pool advisory lock M2-08 added serializes duplicates
 * so a manual retry is safe, but auto-retry on transient 502s could
 * spam Proxmox during a flaky moment.
 */
export function useConnectMutation() {
  const client = useBrokerClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (poolId: string) =>
      client.post<ConnectResponse>(`/api/v1/me/desktops/${poolId}/connect`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: desktopsKeys.all });
    },
  });
}
