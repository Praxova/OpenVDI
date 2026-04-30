import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useBrokerClient } from "@/api/client";
import { adminKeys } from "./dashboard";
import type {
  TemplateCreate,
  TemplateRead,
  TemplateUpdate,
  TemplateValidationResult,
} from "@/types/admin";


// Per-resource key. Parallel to adminKeys.clusters from M4-18.
const templatesKey = ["admin", "templates"] as const;


export function useTemplatesQuery() {
  const client = useBrokerClient();
  return useQuery({
    queryKey: templatesKey,
    queryFn: () => client.get<TemplateRead[]>("/api/v1/templates"),
  });
}


export function useTemplateDetailQuery(id: string | undefined) {
  const client = useBrokerClient();
  return useQuery({
    queryKey: [...templatesKey, id] as const,
    queryFn: () => client.get<TemplateRead>(`/api/v1/templates/${id}`),
    enabled: id !== undefined,
  });
}


export function useCreateTemplateMutation() {
  const client = useBrokerClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: TemplateCreate) =>
      client.post<TemplateRead>("/api/v1/templates", data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: templatesKey });
      queryClient.invalidateQueries({ queryKey: adminKeys.dashboard });
    },
  });
}


export function useUpdateTemplateMutation() {
  const client = useBrokerClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: TemplateUpdate }) =>
      client.put<TemplateRead>(`/api/v1/templates/${id}`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: templatesKey });
    },
  });
}


export function useDeleteTemplateMutation() {
  const client = useBrokerClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      client.delete<void>(`/api/v1/templates/${id}`),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: templatesKey });
      queryClient.invalidateQueries({ queryKey: adminKeys.dashboard });
    },
  });
}


/**
 * Validate a template against the live cluster. Modeled as a mutation
 * (not a query) because: triggered manually, result is point-in-time,
 * re-runs are user-initiated. The result lives on `mutation.data`
 * until the page unmounts.
 */
export function useValidateTemplateMutation() {
  const client = useBrokerClient();
  return useMutation({
    mutationFn: (id: string) =>
      client.post<TemplateValidationResult>(
        `/api/v1/templates/${id}/validate`,
        {},
      ),
  });
}
