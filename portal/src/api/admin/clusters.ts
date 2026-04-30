import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useBrokerClient } from "@/api/client";
import { adminKeys } from "./dashboard"; // shared root from M4-18
import type {
  ClusterCreate,
  ClusterRead,
  ClusterUpdate,
} from "@/types/admin";


/**
 * List clusters. Used by ClustersPage and the dashboard's
 * cluster-health card. (M4-18 originally placed this in
 * api/admin/dashboard.ts; M4-19 moves it here as the canonical home.)
 */
export function useClustersQuery() {
  const client = useBrokerClient();
  return useQuery({
    queryKey: adminKeys.clusters,
    queryFn: () => client.get<ClusterRead[]>("/api/v1/clusters"),
  });
}


/**
 * Fetch one cluster by id. Used by ClusterFormPage in edit mode to
 * pre-populate the form. `enabled: id !== undefined` lets the hook
 * be safely mounted on the create-mode page (no fetch fires).
 */
export function useClusterDetailQuery(id: string | undefined) {
  const client = useBrokerClient();
  return useQuery({
    queryKey: [...adminKeys.clusters, id] as const,
    queryFn: () => client.get<ClusterRead>(`/api/v1/clusters/${id}`),
    enabled: id !== undefined,
  });
}


export function useCreateClusterMutation() {
  const client = useBrokerClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: ClusterCreate) =>
      client.post<ClusterRead>("/api/v1/clusters", data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: adminKeys.clusters });
      // Dashboard summary also reflects cluster count.
      queryClient.invalidateQueries({ queryKey: adminKeys.dashboard });
    },
  });
}


export function useUpdateClusterMutation() {
  const client = useBrokerClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: ClusterUpdate }) =>
      client.put<ClusterRead>(`/api/v1/clusters/${id}`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: adminKeys.clusters });
      queryClient.invalidateQueries({ queryKey: adminKeys.dashboard });
    },
  });
}


export function useDeleteClusterMutation() {
  const client = useBrokerClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      client.delete<void>(`/api/v1/clusters/${id}`),
    onSettled: () => {
      // Whether delete succeeded or failed (409 on pool dependency),
      // refresh the list — the user wants to see current state.
      queryClient.invalidateQueries({ queryKey: adminKeys.clusters });
      queryClient.invalidateQueries({ queryKey: adminKeys.dashboard });
    },
  });
}
