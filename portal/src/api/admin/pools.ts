import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useBrokerClient } from "@/api/client";
import { adminKeys } from "./dashboard";
import type { PoolCreate, PoolRead, PoolUpdate } from "@/types/admin";


const poolsKey = ["admin", "pools"] as const;


export function usePoolsQuery() {
  const client = useBrokerClient();
  return useQuery({
    queryKey: poolsKey,
    queryFn: () => client.get<PoolRead[]>("/api/v1/pools"),
  });
}


export function usePoolDetailQuery(id: string | undefined) {
  const client = useBrokerClient();
  return useQuery({
    queryKey: [...poolsKey, id] as const,
    queryFn: () => client.get<PoolRead>(`/api/v1/pools/${id}`),
    enabled: id !== undefined,
  });
}


export function useCreatePoolMutation() {
  const client = useBrokerClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: PoolCreate) =>
      client.post<PoolRead>("/api/v1/pools", data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: poolsKey });
      qc.invalidateQueries({ queryKey: adminKeys.dashboard });
    },
  });
}


export function useUpdatePoolMutation() {
  const client = useBrokerClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: PoolUpdate }) =>
      client.put<PoolRead>(`/api/v1/pools/${id}`, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: poolsKey });
      qc.invalidateQueries({ queryKey: adminKeys.dashboard });
    },
  });
}


export function useDeletePoolMutation() {
  const client = useBrokerClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      client.delete<void>(`/api/v1/pools/${id}`),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: poolsKey });
      qc.invalidateQueries({ queryKey: adminKeys.dashboard });
    },
  });
}


/**
 * Trigger pool provisioning. POST returns 202 Accepted; the actual
 * provisioning runs in the broker's background tasks. The mutation
 * succeeds when the broker accepts the request — completion happens
 * out-of-band and surfaces via TanStack staleTime refetches of the
 * pools list.
 *
 * The broker's ProvisionRequest schema requires `count` (1..50);
 * caller decides the value.
 */
export function useProvisionPoolMutation() {
  const client = useBrokerClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, count }: { id: string; count: number }) =>
      client.post<unknown>(`/api/v1/pools/${id}/provision`, { count }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: poolsKey });
    },
  });
}


export function useDrainPoolMutation() {
  const client = useBrokerClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      client.post<unknown>(`/api/v1/pools/${id}/drain`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: poolsKey });
    },
  });
}
