import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useBrokerClient } from "@/api/client";
import { adminKeys } from "./dashboard";
import type {
  SessionReadAdmin,
  SessionReadDetailed,
  SessionStatus,
} from "@/types/admin";


const sessionsKey = ["admin", "sessions"] as const;


export type TimePreset = "24h" | "7d" | "30d" | "all";


export interface SessionListFilters {
  username?: string;
  pool_id?: string;
  status?: SessionStatus;
  since?: string; // ISO timestamp, computed from the preset
  include_ended?: boolean;
}


export function useSessionsQuery(filters: SessionListFilters) {
  const client = useBrokerClient();
  const params = new URLSearchParams();
  if (filters.username) params.set("username", filters.username);
  if (filters.pool_id) params.set("pool_id", filters.pool_id);
  if (filters.status) params.set("status", filters.status);
  if (filters.since) params.set("since", filters.since);
  if (filters.include_ended !== undefined) {
    params.set("include_ended", String(filters.include_ended));
  }
  params.set("limit", "50");
  params.set("sort", "created_at");
  params.set("order", "desc");
  return useQuery({
    queryKey: [...sessionsKey, "list", filters] as const,
    queryFn: () =>
      client.get<SessionReadAdmin[]>(
        `/api/v1/sessions?${params.toString()}`,
      ),
  });
}


/**
 * Detail query, used by the drawer. `staleTime: 0` so each open
 * forces a fresh fetch — useful while a session is live and telemetry
 * is moving.
 */
export function useSessionDetailQuery(id: string | null) {
  const client = useBrokerClient();
  return useQuery({
    queryKey: [...sessionsKey, "detail", id] as const,
    queryFn: () =>
      client.get<SessionReadDetailed>(`/api/v1/sessions/${id}`),
    enabled: id !== null,
    staleTime: 0,
  });
}


/**
 * Force-disconnect. Returns 204 on success (idempotent on already-
 * ended). Invalidates both the list and dashboard summary because
 * the latter shows an active-session count.
 */
export function useForceDisconnectMutation() {
  const client = useBrokerClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      client.delete<void>(`/api/v1/sessions/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: sessionsKey });
      qc.invalidateQueries({ queryKey: adminKeys.dashboard });
    },
  });
}


/**
 * Convert a TimePreset to an ISO `since` value. `all` returns
 * undefined (the broker's default = unbounded). Memoize the result on
 * the preset value, NOT on `Date.now()` — otherwise the timestamp
 * advances every render and busts the query cache key.
 */
export function presetToSince(preset: TimePreset): string | undefined {
  const now = Date.now();
  switch (preset) {
    case "24h":
      return new Date(now - 24 * 60 * 60 * 1000).toISOString();
    case "7d":
      return new Date(now - 7 * 24 * 60 * 60 * 1000).toISOString();
    case "30d":
      return new Date(now - 30 * 24 * 60 * 60 * 1000).toISOString();
    case "all":
      return undefined;
  }
}
