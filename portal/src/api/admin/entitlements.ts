import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useBrokerClient } from "@/api/client";
import type { EntitlementCreate, EntitlementRead } from "@/types/admin";


const entitlementsKey = (poolId: string) =>
  ["admin", "pools", poolId, "entitlements"] as const;


export function usePoolEntitlementsQuery(poolId: string | undefined) {
  const client = useBrokerClient();
  return useQuery({
    queryKey:
      poolId !== undefined
        ? entitlementsKey(poolId)
        : (["admin", "pools", "_none", "entitlements"] as const),
    queryFn: () =>
      client.get<EntitlementRead[]>(`/api/v1/pools/${poolId}/entitlements`),
    enabled: poolId !== undefined,
  });
}


export function useGrantEntitlementMutation(poolId: string) {
  const client = useBrokerClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: EntitlementCreate) =>
      client.post<EntitlementRead>(
        `/api/v1/pools/${poolId}/entitlements`,
        data,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: entitlementsKey(poolId) });
    },
  });
}


export function useRevokeEntitlementMutation(poolId: string) {
  const client = useBrokerClient();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (entitlementId: string) =>
      client.delete<void>(
        `/api/v1/pools/${poolId}/entitlements/${entitlementId}`,
      ),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: entitlementsKey(poolId) });
    },
  });
}
