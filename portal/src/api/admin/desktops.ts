import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useBrokerClient } from "@/api/client";
import { adminKeys } from "./dashboard";
import type {
  DesktopAssignRequest,
  DesktopRead,
  DesktopReadDetailed,
  DesktopStatus,
  PowerAction,
} from "@/types/admin";


const desktopsKey = ["admin", "desktops"] as const;


export interface DesktopListFilters {
  pool_id?: string;
  status?: DesktopStatus;
  assigned_user?: string;
}


export function useDesktopsQuery(filters: DesktopListFilters) {
  const client = useBrokerClient();
  const params = new URLSearchParams();
  if (filters.pool_id) params.set("pool_id", filters.pool_id);
  if (filters.status) params.set("status", filters.status);
  if (filters.assigned_user) {
    params.set("assigned_user", filters.assigned_user);
  }
  params.set("limit", "50");
  return useQuery({
    queryKey: [...desktopsKey, "list", filters] as const,
    queryFn: () =>
      client.get<DesktopRead[]>(`/api/v1/desktops?${params.toString()}`),
  });
}


/**
 * Detail query, used by the side-panel drawer. `staleTime: 0` so each
 * drawer open re-fetches; combined with the broker's opportunistic
 * power-state reconcile, this keeps the drawer's view fresh.
 */
export function useDesktopDetailQuery(id: string | null) {
  const client = useBrokerClient();
  return useQuery({
    queryKey: [...desktopsKey, "detail", id] as const,
    queryFn: () =>
      client.get<DesktopReadDetailed>(`/api/v1/desktops/${id}`),
    enabled: id !== null,
    staleTime: 0,
  });
}


export function useAssignDesktopMutation() {
  const client = useBrokerClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      data,
    }: {
      id: string;
      data: DesktopAssignRequest;
    }) =>
      client.post<DesktopRead>(`/api/v1/desktops/${id}/assign`, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: desktopsKey });
    },
  });
}


export function useUnassignDesktopMutation() {
  const client = useBrokerClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      client.post<DesktopRead>(`/api/v1/desktops/${id}/unassign`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: desktopsKey });
    },
  });
}


export function useDesktopPowerMutation() {
  const client = useBrokerClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, action }: { id: string; action: PowerAction }) =>
      client.post<unknown>(`/api/v1/desktops/${id}/power/${action}`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: desktopsKey });
    },
  });
}


export function useRebuildDesktopMutation() {
  const client = useBrokerClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      client.post<unknown>(`/api/v1/desktops/${id}/rebuild`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: desktopsKey });
      qc.invalidateQueries({ queryKey: adminKeys.dashboard });
    },
  });
}


export function useDestroyDesktopMutation() {
  const client = useBrokerClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      client.delete<void>(`/api/v1/desktops/${id}`),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: desktopsKey });
      qc.invalidateQueries({ queryKey: adminKeys.dashboard });
    },
  });
}
