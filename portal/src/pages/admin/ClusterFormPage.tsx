import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ChevronLeft } from "lucide-react";

import {
  useClusterDetailQuery,
  useCreateClusterMutation,
  useUpdateClusterMutation,
} from "@/api/admin/clusters";
import { brokerErrorCode } from "@/api/errors";
import { FormField } from "@/components/admin/FormField";
import type { ClusterCreate, ClusterUpdate } from "@/types/admin";


interface FormState {
  name: string;
  api_url: string;
  token_id: string;
  token_secret: string;
  verify_ssl: boolean;
  node_filter: string;
}


const EMPTY_FORM: FormState = {
  name: "",
  api_url: "",
  token_id: "",
  token_secret: "",
  verify_ssl: true,
  node_filter: "",
};


export function ClusterFormPage() {
  const { id } = useParams<{ id?: string }>();
  const isEdit = id !== undefined;
  const navigate = useNavigate();
  const detailQuery = useClusterDetailQuery(id);
  const createMutation = useCreateClusterMutation();
  const updateMutation = useUpdateClusterMutation();

  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Pre-populate form on edit-mode detail fetch.
  useEffect(() => {
    if (!isEdit || detailQuery.data === undefined) return;
    const c = detailQuery.data;
    setForm({
      name: c.name,
      api_url: c.api_url,
      token_id: c.token_id,
      token_secret: "", // Per FE8: edit-mode renders empty.
      verify_ssl: c.verify_ssl,
      node_filter: c.node_filter ?? "",
    });
  }, [isEdit, detailQuery.data]);

  const isLoading = isEdit && detailQuery.isPending;
  const submitting = createMutation.isPending || updateMutation.isPending;

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    setSubmitError(null);

    try {
      if (isEdit && id !== undefined) {
        // Build a partial update; per FE8, omit token_secret when empty.
        const payload: ClusterUpdate = {
          name: form.name,
          api_url: form.api_url,
          token_id: form.token_id,
          verify_ssl: form.verify_ssl,
          node_filter: form.node_filter || null,
        };
        if (form.token_secret !== "") {
          payload.token_secret = form.token_secret;
        }
        await updateMutation.mutateAsync({ id, data: payload });
      } else {
        const payload: ClusterCreate = {
          name: form.name,
          api_url: form.api_url,
          token_id: form.token_id,
          token_secret: form.token_secret,
          verify_ssl: form.verify_ssl,
        };
        if (form.node_filter !== "") {
          payload.node_filter = form.node_filter;
        }
        await createMutation.mutateAsync(payload);
      }
      navigate("/admin/clusters");
    } catch (exc) {
      setSubmitError(formatSubmitError(exc));
    }
  };

  if (isLoading) {
    return (
      <div className="px-6 py-8 max-w-2xl mx-auto">
        <p className="text-text-secondary">Loading cluster…</p>
      </div>
    );
  }
  if (isEdit && detailQuery.error !== null) {
    return (
      <div className="px-6 py-8 max-w-2xl mx-auto">
        <p role="alert" className="text-danger-fg">
          Couldn't load cluster: {detailQuery.error.message}
        </p>
        <Link
          to="/admin/clusters"
          className="text-action-primary mt-4 inline-block"
        >
          ← Back to clusters
        </Link>
      </div>
    );
  }

  return (
    <div className="px-6 py-8">
      <div className="max-w-2xl mx-auto">
        <Link
          to="/admin/clusters"
          className="inline-flex items-center gap-1 text-text-secondary hover:text-text-primary mb-4"
        >
          <ChevronLeft size={16} aria-hidden />
          <span className="text-body-sm">Back to clusters</span>
        </Link>

        <header className="mb-6">
          <h1 className="font-display text-h1 font-semibold text-text-primary">
            {isEdit ? "Edit cluster" : "Add cluster"}
          </h1>
          {!isEdit && (
            <p className="text-body text-text-secondary mt-2">
              The broker validates credentials by pinging the cluster on
              save. Submission may take a few seconds.
            </p>
          )}
        </header>

        <form
          onSubmit={handleSubmit}
          className={
            "bg-surface-1 border border-border-subtle rounded-lg p-6 " +
            "flex flex-col gap-5"
          }
        >
          {submitError !== null && (
            <div
              role="alert"
              className={
                "px-3 py-2 rounded-md text-body-sm " +
                "bg-danger-bg border border-danger-border text-danger-fg"
              }
            >
              {submitError}
            </div>
          )}

          <FormField label="Name" htmlFor="cluster-name" required>
            <input
              id="cluster-name"
              type="text"
              required
              maxLength={128}
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className={inputClass}
            />
          </FormField>

          <FormField
            label="API URL"
            htmlFor="cluster-api-url"
            required
            hint="e.g. https://pve1.example.com:8006"
          >
            <input
              id="cluster-api-url"
              type="url"
              required
              value={form.api_url}
              onChange={(e) =>
                setForm({ ...form, api_url: e.target.value })
              }
              className={inputClass}
            />
          </FormField>

          <FormField
            label="Token ID"
            htmlFor="cluster-token-id"
            required
            hint="Proxmox token format: user@realm!tokenid"
          >
            <input
              id="cluster-token-id"
              type="text"
              required
              value={form.token_id}
              onChange={(e) =>
                setForm({ ...form, token_id: e.target.value })
              }
              className={inputClass}
            />
          </FormField>

          <FormField
            label="Token Secret"
            htmlFor="cluster-token-secret"
            required={!isEdit}
            hint={
              isEdit
                ? "Leave blank to keep the existing secret."
                : "The Proxmox API token's secret value."
            }
          >
            <input
              id="cluster-token-secret"
              type="password"
              autoComplete="new-password"
              required={!isEdit}
              value={form.token_secret}
              onChange={(e) =>
                setForm({ ...form, token_secret: e.target.value })
              }
              className={inputClass}
            />
          </FormField>

          <FormField
            label="Verify SSL"
            htmlFor="cluster-verify-ssl"
            hint="Disable for self-signed Proxmox certificates in dev."
          >
            <span className="inline-flex items-center gap-2">
              <input
                id="cluster-verify-ssl"
                type="checkbox"
                checked={form.verify_ssl}
                onChange={(e) =>
                  setForm({ ...form, verify_ssl: e.target.checked })
                }
                className="h-4 w-4"
              />
              <span className="text-body-sm text-text-primary">
                {form.verify_ssl
                  ? "Verify SSL certificate"
                  : "Skip verification"}
              </span>
            </span>
          </FormField>

          <FormField
            label="Node filter"
            htmlFor="cluster-node-filter"
            hint="Optional. Comma-separated node names to limit the cluster to."
          >
            <input
              id="cluster-node-filter"
              type="text"
              value={form.node_filter}
              onChange={(e) =>
                setForm({ ...form, node_filter: e.target.value })
              }
              className={inputClass}
            />
          </FormField>

          <div className="flex items-center justify-end gap-3 pt-2">
            <Link
              to="/admin/clusters"
              className={
                "h-10 px-4 inline-flex items-center rounded-md " +
                "text-text-secondary hover:bg-surface-2 hover:text-text-primary " +
                "focus-visible:outline-none focus-visible:shadow-focus"
              }
            >
              Cancel
            </Link>
            <button
              type="submit"
              disabled={submitting}
              className={
                "h-10 px-4 rounded-md " +
                "bg-action-primary text-text-on-accent text-body font-medium " +
                "hover:bg-action-primary-hover " +
                "focus-visible:outline-none focus-visible:shadow-focus " +
                "disabled:opacity-50 disabled:cursor-not-allowed"
              }
            >
              {submitting
                ? isEdit
                  ? "Saving…"
                  : "Creating…"
                : isEdit
                  ? "Save changes"
                  : "Create cluster"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}


const inputClass =
  "h-10 px-3 rounded-md bg-bg border border-border-subtle " +
  "text-text-primary text-body " +
  "focus-visible:outline-none focus-visible:shadow-focus";


function formatSubmitError(exc: unknown): string {
  switch (brokerErrorCode(exc)) {
    case "INVALID_REQUEST":
      return "One or more fields are invalid. Check the values and try again.";
    case "CONFLICT":
      return "A cluster with this name already exists.";
    case "PROVIDER_AUTH":
      return "The credentials don't authenticate against the cluster's API.";
    case "PROVIDER_ERROR":
    case "SERVICE_UNAVAILABLE":
      return "Couldn't reach the cluster. Check the API URL and try again.";
    default:
      return "Failed to save. Check broker logs for details.";
  }
}
