import {
  useEffect,
  useMemo,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ChevronLeft } from "lucide-react";

import { useClustersQuery } from "@/api/admin/clusters";
import {
  useCreatePoolMutation,
  usePoolDetailQuery,
  useUpdatePoolMutation,
} from "@/api/admin/pools";
import { useTemplatesQuery } from "@/api/admin/templates";
import { brokerErrorCode } from "@/api/errors";
import { FormField } from "@/components/admin/FormField";
import { POOL_NAME_PATTERN } from "@/types/admin";
import type {
  PoolCreate,
  PoolStatus,
  PoolType,
  PoolUpdate,
} from "@/types/admin";

import { EntitlementsPanel } from "./EntitlementsPanel";


interface FormState {
  // Identity
  cluster_id: string;
  template_id: string;
  pool_type: PoolType;
  name: string;
  display_name: string;
  description: string;
  // Capacity / range
  vmid_range_start: string;
  vmid_range_end: string;
  min_spare: string;
  max_size: string;
  // Naming
  name_prefix: string;
  // Placement
  target_nodes: string;
  // Per-VM overrides (optional)
  cpu_cores: string;
  memory_mb: string;
  // Logoff
  auto_logoff_min: string;
  delete_on_logoff: boolean;
  refresh_on_logoff: boolean;
  // Edit-only
  status: PoolStatus;
}


const EMPTY_FORM: FormState = {
  cluster_id: "",
  template_id: "",
  pool_type: "nonpersistent",
  name: "",
  display_name: "",
  description: "",
  vmid_range_start: "",
  vmid_range_end: "",
  min_spare: "1",
  max_size: "10",
  name_prefix: "",
  target_nodes: "",
  cpu_cores: "",
  memory_mb: "",
  auto_logoff_min: "0",
  delete_on_logoff: false,
  refresh_on_logoff: true,
  status: "active",
};


export function PoolFormPage() {
  const { id } = useParams<{ id?: string }>();
  const isEdit = id !== undefined;
  const navigate = useNavigate();

  const detailQuery = usePoolDetailQuery(id);
  const clusters = useClustersQuery();
  const templates = useTemplatesQuery();
  const createMutation = useCreatePoolMutation();
  const updateMutation = useUpdatePoolMutation();

  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    if (!isEdit || detailQuery.data === undefined) return;
    const p = detailQuery.data;
    setForm({
      cluster_id: p.cluster_id,
      template_id: p.template_id,
      pool_type: p.pool_type,
      name: p.name,
      display_name: p.display_name,
      description: p.description ?? "",
      vmid_range_start: String(p.vmid_range_start),
      vmid_range_end: String(p.vmid_range_end),
      min_spare: String(p.min_spare),
      max_size: String(p.max_size),
      name_prefix: p.name_prefix,
      target_nodes: p.target_nodes ?? "",
      cpu_cores: p.cpu_cores !== null ? String(p.cpu_cores) : "",
      memory_mb: p.memory_mb !== null ? String(p.memory_mb) : "",
      auto_logoff_min: String(p.auto_logoff_min),
      delete_on_logoff: p.delete_on_logoff,
      refresh_on_logoff: p.refresh_on_logoff,
      status: p.status,
    });
  }, [isEdit, detailQuery.data]);

  const isLoading = isEdit && detailQuery.isPending;
  const submitting = createMutation.isPending || updateMutation.isPending;
  const isPersistent = form.pool_type === "persistent";

  // Templates dropdown is filtered by selected cluster (create only).
  const filteredTemplates = useMemo(() => {
    if (templates.data === undefined) return [];
    return templates.data.filter((t) => t.cluster_id === form.cluster_id);
  }, [templates.data, form.cluster_id]);

  // Resolve cluster + template for read-only display in edit mode.
  const cluster = useMemo(() => {
    if (clusters.data === undefined || form.cluster_id === "") return undefined;
    return clusters.data.find((c) => c.id === form.cluster_id);
  }, [clusters.data, form.cluster_id]);
  const template = useMemo(() => {
    if (templates.data === undefined || form.template_id === "") return undefined;
    return templates.data.find((t) => t.id === form.template_id);
  }, [templates.data, form.template_id]);

  const handleClusterChange = (value: string) => {
    // Reset template when cluster changes — stale template_id from a
    // different cluster would fail validation.
    setForm({ ...form, cluster_id: value, template_id: "" });
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    setSubmitError(null);

    try {
      if (isEdit && id !== undefined) {
        // PoolUpdate: omit immutable fields entirely.
        const payload: PoolUpdate = {
          name: form.name,
          display_name: form.display_name,
          description: form.description !== "" ? form.description : null,
          min_spare: parseInt(form.min_spare, 10),
          max_size: parseInt(form.max_size, 10),
          name_prefix: form.name_prefix,
          target_nodes:
            form.target_nodes !== "" ? form.target_nodes : null,
          cpu_cores:
            form.cpu_cores !== "" ? parseInt(form.cpu_cores, 10) : null,
          memory_mb:
            form.memory_mb !== "" ? parseInt(form.memory_mb, 10) : null,
          auto_logoff_min: parseInt(form.auto_logoff_min, 10),
          delete_on_logoff: form.delete_on_logoff,
          refresh_on_logoff: form.refresh_on_logoff,
          status: form.status,
        };
        await updateMutation.mutateAsync({ id, data: payload });
      } else {
        const payload: PoolCreate = {
          cluster_id: form.cluster_id,
          template_id: form.template_id,
          pool_type: form.pool_type,
          name: form.name,
          display_name: form.display_name,
          vmid_range_start: parseInt(form.vmid_range_start, 10),
          vmid_range_end: parseInt(form.vmid_range_end, 10),
          name_prefix: form.name_prefix,
          min_spare: parseInt(form.min_spare, 10),
          max_size: parseInt(form.max_size, 10),
          auto_logoff_min: parseInt(form.auto_logoff_min, 10),
          delete_on_logoff: form.delete_on_logoff,
          refresh_on_logoff: form.refresh_on_logoff,
        };
        if (form.description !== "") payload.description = form.description;
        if (form.target_nodes !== "")
          payload.target_nodes = form.target_nodes;
        if (form.cpu_cores !== "")
          payload.cpu_cores = parseInt(form.cpu_cores, 10);
        if (form.memory_mb !== "")
          payload.memory_mb = parseInt(form.memory_mb, 10);
        await createMutation.mutateAsync(payload);
      }
      navigate("/admin/pools");
    } catch (exc) {
      setSubmitError(formatSubmitError(exc));
    }
  };

  if (isLoading) {
    return (
      <FormPageShell title="Edit pool">
        <p className="text-text-secondary">Loading…</p>
      </FormPageShell>
    );
  }
  if (isEdit && detailQuery.error !== null) {
    return (
      <FormPageShell title="Edit pool">
        <p role="alert" className="text-danger-fg">
          Couldn't load pool: {detailQuery.error.message}
        </p>
      </FormPageShell>
    );
  }

  return (
    <FormPageShell title={isEdit ? "Edit pool" : "Add pool"}>
      <form
        onSubmit={handleSubmit}
        className={
          "bg-surface-1 border border-border-subtle rounded-lg p-6 " +
          "flex flex-col gap-6"
        }
      >
        {submitError !== null && (
          <div role="alert" className={alertBanner}>
            {submitError}
          </div>
        )}

        <fieldset className="flex flex-col gap-5 border-0 p-0">
          <legend className={legendClass}>Identity</legend>

          {isEdit ? (
            <FormField label="Cluster" htmlFor="pool-cluster">
              <input
                id="pool-cluster"
                type="text"
                readOnly
                disabled
                value={cluster?.name ?? "(unknown)"}
                className={inputClassReadOnly}
              />
            </FormField>
          ) : (
            <FormField label="Cluster" htmlFor="pool-cluster" required>
              <select
                id="pool-cluster"
                required
                value={form.cluster_id}
                onChange={(e) => handleClusterChange(e.target.value)}
                className={inputClass}
              >
                <option value="">Select a cluster…</option>
                {clusters.data?.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </FormField>
          )}

          {isEdit ? (
            <FormField label="Template" htmlFor="pool-template">
              <input
                id="pool-template"
                type="text"
                readOnly
                disabled
                value={template?.name ?? "(unknown)"}
                className={inputClassReadOnly}
              />
            </FormField>
          ) : (
            <FormField
              label="Template"
              htmlFor="pool-template"
              required
              hint={
                form.cluster_id === ""
                  ? "Pick a cluster first."
                  : filteredTemplates.length === 0
                    ? "No templates registered for this cluster."
                    : undefined
              }
            >
              <select
                id="pool-template"
                required
                value={form.template_id}
                onChange={(e) =>
                  setForm({ ...form, template_id: e.target.value })
                }
                disabled={
                  form.cluster_id === "" || filteredTemplates.length === 0
                }
                className={inputClass}
              >
                <option value="">Select a template…</option>
                {filteredTemplates.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </FormField>
          )}

          {isEdit ? (
            <FormField label="Pool type" htmlFor="pool-type">
              <input
                id="pool-type"
                type="text"
                readOnly
                disabled
                value={form.pool_type}
                className={inputClassReadOnly}
              />
            </FormField>
          ) : (
            <FormField label="Pool type" htmlFor="pool-type-radio" required>
              <div
                role="radiogroup"
                aria-labelledby="pool-type-radio"
                className="inline-flex items-center gap-1 p-1 rounded-md bg-surface-2"
              >
                {(["nonpersistent", "persistent"] as const).map((value) => (
                  <label
                    key={value}
                    className={
                      "px-3 py-1.5 rounded-sm text-body-sm font-medium cursor-pointer " +
                      "transition-colors duration-fast ease-out " +
                      (form.pool_type === value
                        ? "bg-bg text-text-primary shadow-sm"
                        : "text-text-secondary hover:text-text-primary")
                    }
                  >
                    <input
                      type="radio"
                      name="pool_type"
                      value={value}
                      checked={form.pool_type === value}
                      onChange={() =>
                        setForm({ ...form, pool_type: value })
                      }
                      className="sr-only"
                    />
                    {value === "nonpersistent"
                      ? "Non-persistent"
                      : "Persistent"}
                  </label>
                ))}
              </div>
            </FormField>
          )}

          <FormField
            label="Name (slug)"
            htmlFor="pool-name"
            required
            hint="Lowercase letters, digits, hyphens, underscores. Used as the Proxmox tag fragment."
          >
            <input
              id="pool-name"
              type="text"
              required
              minLength={1}
              maxLength={64}
              pattern={POOL_NAME_PATTERN}
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className={inputClass}
            />
          </FormField>

          <FormField label="Display name" htmlFor="pool-display-name" required>
            <input
              id="pool-display-name"
              type="text"
              required
              maxLength={256}
              value={form.display_name}
              onChange={(e) =>
                setForm({ ...form, display_name: e.target.value })
              }
              className={inputClass}
            />
          </FormField>

          <FormField label="Description" htmlFor="pool-description">
            <input
              id="pool-description"
              type="text"
              maxLength={1024}
              value={form.description}
              onChange={(e) =>
                setForm({ ...form, description: e.target.value })
              }
              className={inputClass}
            />
          </FormField>
        </fieldset>

        <fieldset className="flex flex-col gap-5 border-0 p-0">
          <legend className={legendClass}>VMID range + capacity</legend>

          <div className="grid grid-cols-2 gap-4">
            <FormField
              label="VMID range start"
              htmlFor="pool-vmid-start"
              required={!isEdit}
              hint={isEdit ? "Immutable post-creation." : undefined}
            >
              <input
                id="pool-vmid-start"
                type="number"
                min={1}
                required={!isEdit}
                disabled={isEdit}
                value={form.vmid_range_start}
                onChange={(e) =>
                  setForm({ ...form, vmid_range_start: e.target.value })
                }
                className={isEdit ? inputClassReadOnly : inputClass}
              />
            </FormField>
            <FormField
              label="VMID range end"
              htmlFor="pool-vmid-end"
              required={!isEdit}
              hint={isEdit ? "Immutable post-creation." : undefined}
            >
              <input
                id="pool-vmid-end"
                type="number"
                min={1}
                required={!isEdit}
                disabled={isEdit}
                value={form.vmid_range_end}
                onChange={(e) =>
                  setForm({ ...form, vmid_range_end: e.target.value })
                }
                className={isEdit ? inputClassReadOnly : inputClass}
              />
            </FormField>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <FormField label="Min spare" htmlFor="pool-min-spare" required>
              <input
                id="pool-min-spare"
                type="number"
                min={0}
                required
                value={form.min_spare}
                onChange={(e) =>
                  setForm({ ...form, min_spare: e.target.value })
                }
                className={inputClass}
              />
            </FormField>
            <FormField label="Max size" htmlFor="pool-max-size" required>
              <input
                id="pool-max-size"
                type="number"
                min={1}
                required
                value={form.max_size}
                onChange={(e) =>
                  setForm({ ...form, max_size: e.target.value })
                }
                className={inputClass}
              />
            </FormField>
          </div>

          <FormField
            label="Name prefix"
            htmlFor="pool-name-prefix"
            required
            hint="Prefix for cloned VM names (e.g. ENG → ENG-001, ENG-002)."
          >
            <input
              id="pool-name-prefix"
              type="text"
              required
              maxLength={64}
              value={form.name_prefix}
              onChange={(e) =>
                setForm({ ...form, name_prefix: e.target.value })
              }
              className={inputClass}
            />
          </FormField>
        </fieldset>

        <fieldset className="flex flex-col gap-5 border-0 p-0">
          <legend className={legendClass}>Placement</legend>
          <FormField
            label="Target nodes"
            htmlFor="pool-target-nodes"
            hint="Comma-separated. Empty = any node in the cluster."
          >
            <input
              id="pool-target-nodes"
              type="text"
              value={form.target_nodes}
              onChange={(e) =>
                setForm({ ...form, target_nodes: e.target.value })
              }
              className={inputClass}
            />
          </FormField>
        </fieldset>

        <fieldset className="flex flex-col gap-5 border-0 p-0">
          <legend className={legendClass}>Per-VM overrides (optional)</legend>
          <div className="grid grid-cols-2 gap-4">
            <FormField
              label="CPU cores"
              htmlFor="pool-cpu-cores"
              hint="Empty = inherit from template."
            >
              <input
                id="pool-cpu-cores"
                type="number"
                min={1}
                value={form.cpu_cores}
                onChange={(e) =>
                  setForm({ ...form, cpu_cores: e.target.value })
                }
                className={inputClass}
              />
            </FormField>
            <FormField
              label="Memory (MB)"
              htmlFor="pool-memory-mb"
              hint="Empty = inherit from template."
            >
              <input
                id="pool-memory-mb"
                type="number"
                min={512}
                value={form.memory_mb}
                onChange={(e) =>
                  setForm({ ...form, memory_mb: e.target.value })
                }
                className={inputClass}
              />
            </FormField>
          </div>
        </fieldset>

        <fieldset className="flex flex-col gap-5 border-0 p-0">
          <legend className={legendClass}>Logoff behavior</legend>

          <FormField
            label="Auto logoff (minutes)"
            htmlFor="pool-auto-logoff"
            hint="0 = disabled."
          >
            <input
              id="pool-auto-logoff"
              type="number"
              min={0}
              value={form.auto_logoff_min}
              onChange={(e) =>
                setForm({ ...form, auto_logoff_min: e.target.value })
              }
              className={inputClass}
            />
          </FormField>

          <FormField
            label="Refresh on logoff"
            htmlFor="pool-refresh-on-logoff"
            hint={
              isPersistent
                ? "Only applies to non-persistent pools."
                : "Roll back to the openvdi-base snapshot when the user logs off."
            }
          >
            <span className="inline-flex items-center gap-2">
              <input
                id="pool-refresh-on-logoff"
                type="checkbox"
                disabled={isPersistent}
                checked={form.refresh_on_logoff}
                onChange={(e) =>
                  setForm({ ...form, refresh_on_logoff: e.target.checked })
                }
                className="h-4 w-4"
              />
              <span className="text-body-sm text-text-primary">
                Roll back desktop on logoff
              </span>
            </span>
          </FormField>

          <FormField
            label="Delete on logoff"
            htmlFor="pool-delete-on-logoff"
            hint={
              isPersistent
                ? "Only applies to non-persistent pools."
                : "Destroy the desktop on logoff. The pool provisioner replaces it."
            }
          >
            <span className="inline-flex items-center gap-2">
              <input
                id="pool-delete-on-logoff"
                type="checkbox"
                disabled={isPersistent}
                checked={form.delete_on_logoff}
                onChange={(e) =>
                  setForm({ ...form, delete_on_logoff: e.target.checked })
                }
                className="h-4 w-4"
              />
              <span className="text-body-sm text-text-primary">
                Destroy desktop on logoff
              </span>
            </span>
          </FormField>
        </fieldset>

        {isEdit && (
          <FormField label="Status" htmlFor="pool-status">
            <select
              id="pool-status"
              value={form.status}
              onChange={(e) =>
                setForm({
                  ...form,
                  status: e.target.value as PoolStatus,
                })
              }
              className={inputClass}
            >
              <option value="active">Active</option>
              <option value="disabled">Disabled</option>
              <option value="draining">Draining</option>
              <option value="error">Error</option>
            </select>
          </FormField>
        )}

        <div className="flex items-center justify-end gap-3 pt-2">
          <Link to="/admin/pools" className={cancelBtn}>
            Cancel
          </Link>
          <button
            type="submit"
            disabled={submitting}
            className={primaryBtn}
          >
            {submitting
              ? isEdit
                ? "Saving…"
                : "Creating…"
              : isEdit
                ? "Save changes"
                : "Create pool"}
          </button>
        </div>
      </form>

      {isEdit && id !== undefined && <EntitlementsPanel poolId={id} />}
    </FormPageShell>
  );
}


function FormPageShell({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="px-6 py-8">
      <div className="max-w-2xl mx-auto flex flex-col gap-6">
        <div>
          <Link to="/admin/pools" className={backLink}>
            <ChevronLeft size={16} aria-hidden />
            <span className="text-body-sm">Back to pools</span>
          </Link>
          <h1 className="font-display text-h1 font-semibold text-text-primary mt-3">
            {title}
          </h1>
        </div>
        {children}
      </div>
    </div>
  );
}


function formatSubmitError(exc: unknown): string {
  switch (brokerErrorCode(exc)) {
    case "INVALID_REQUEST":
      return "One or more fields are invalid. Check the values and try again.";
    case "CONFLICT":
      return (
        "Either the pool name is taken, or the VMID range overlaps " +
        "another pool's range."
      );
    case "NOT_FOUND":
      return "The selected cluster or template no longer exists.";
    case "SERVICE_UNAVAILABLE":
      return "Cluster is offline. The broker can't validate the VMID range.";
    case "PROVIDER_ERROR":
      return (
        "Existing VMs were found in the proposed VMID range. " +
        "Pick a non-overlapping range."
      );
    default:
      return "Failed to save. Check broker logs for details.";
  }
}


// Style constants — duplicated from sibling pages.
const inputClass =
  "h-10 px-3 rounded-md bg-bg border border-border-subtle " +
  "text-text-primary text-body " +
  "focus-visible:outline-none focus-visible:shadow-focus";
const inputClassReadOnly = inputClass + " opacity-60 cursor-not-allowed";
const primaryBtn =
  "h-10 px-4 rounded-md " +
  "bg-action-primary text-text-on-accent text-body font-medium " +
  "hover:bg-action-primary-hover " +
  "focus-visible:outline-none focus-visible:shadow-focus " +
  "disabled:opacity-50 disabled:cursor-not-allowed";
const cancelBtn =
  "h-10 px-4 inline-flex items-center rounded-md " +
  "text-text-secondary hover:bg-surface-2 hover:text-text-primary " +
  "focus-visible:outline-none focus-visible:shadow-focus";
const backLink =
  "inline-flex items-center gap-1 text-text-secondary hover:text-text-primary";
const alertBanner =
  "px-3 py-2 rounded-md text-body-sm " +
  "bg-danger-bg border border-danger-border text-danger-fg";
const legendClass =
  "text-caption uppercase tracking-wide text-text-tertiary font-medium";
