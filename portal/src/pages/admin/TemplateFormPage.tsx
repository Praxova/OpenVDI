import {
  useEffect,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  AlertTriangle,
  CheckCircle,
  ChevronLeft,
  Play,
  XCircle,
} from "lucide-react";

import { useClustersQuery } from "@/api/admin/clusters";
import {
  useCreateTemplateMutation,
  useTemplateDetailQuery,
  useUpdateTemplateMutation,
  useValidateTemplateMutation,
} from "@/api/admin/templates";
import { brokerErrorCode } from "@/api/errors";
import { FormField } from "@/components/admin/FormField";
import { OS_TYPES } from "@/types/admin";
import type {
  TemplateCreate,
  TemplateUpdate,
  TemplateValidationResult,
} from "@/types/admin";


interface FormState {
  cluster_id: string;
  name: string;
  pve_vmid: string; // string-typed in form state; parsed on submit
  pve_node: string;
  os_type: string;
  description: string;
  cpu_cores: string;
  memory_mb: string;
  disk_gb: string;
  gpu_required: boolean;
}


const EMPTY_FORM: FormState = {
  cluster_id: "",
  name: "",
  pve_vmid: "",
  pve_node: "",
  os_type: "windows11",
  description: "",
  cpu_cores: "2",
  memory_mb: "4096",
  disk_gb: "60",
  gpu_required: false,
};


export function TemplateFormPage() {
  const { id } = useParams<{ id?: string }>();
  const isEdit = id !== undefined;
  const navigate = useNavigate();

  const detailQuery = useTemplateDetailQuery(id);
  const clusters = useClustersQuery();
  const createMutation = useCreateTemplateMutation();
  const updateMutation = useUpdateTemplateMutation();
  const validateMutation = useValidateTemplateMutation();

  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Pre-populate on edit-mode detail fetch.
  useEffect(() => {
    if (!isEdit || detailQuery.data === undefined) return;
    const t = detailQuery.data;
    setForm({
      cluster_id: t.cluster_id,
      name: t.name,
      pve_vmid: String(t.pve_vmid),
      pve_node: t.pve_node,
      os_type: t.os_type,
      description: t.description ?? "",
      cpu_cores: String(t.cpu_cores),
      memory_mb: String(t.memory_mb),
      disk_gb: String(t.disk_gb),
      gpu_required: t.gpu_required,
    });
  }, [isEdit, detailQuery.data]);

  const isLoading = isEdit && detailQuery.isPending;
  const submitting = createMutation.isPending || updateMutation.isPending;
  const noClusters =
    clusters.data !== undefined && clusters.data.length === 0;

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    setSubmitError(null);

    try {
      if (isEdit && id !== undefined) {
        // TemplateUpdate: cluster_id and pve_vmid are immutable —
        // omit from payload entirely (broker schema rejects them).
        const payload: TemplateUpdate = {
          name: form.name,
          pve_node: form.pve_node,
          os_type: form.os_type,
          description: form.description !== "" ? form.description : null,
          cpu_cores: parseInt(form.cpu_cores, 10),
          memory_mb: parseInt(form.memory_mb, 10),
          disk_gb: parseInt(form.disk_gb, 10),
          gpu_required: form.gpu_required,
        };
        await updateMutation.mutateAsync({ id, data: payload });
      } else {
        const payload: TemplateCreate = {
          cluster_id: form.cluster_id,
          name: form.name,
          pve_vmid: parseInt(form.pve_vmid, 10),
          pve_node: form.pve_node,
          os_type: form.os_type,
          cpu_cores: parseInt(form.cpu_cores, 10),
          memory_mb: parseInt(form.memory_mb, 10),
          disk_gb: parseInt(form.disk_gb, 10),
          gpu_required: form.gpu_required,
        };
        if (form.description !== "") {
          payload.description = form.description;
        }
        await createMutation.mutateAsync(payload);
      }
      navigate("/admin/templates");
    } catch (exc) {
      setSubmitError(formatSubmitError(exc));
    }
  };

  const handleValidate = async () => {
    if (id === undefined) return;
    try {
      await validateMutation.mutateAsync(id);
    } catch {
      // Error surfaces via validateMutation.error in the panel.
    }
  };

  if (isLoading || (clusters.isPending && !isEdit)) {
    return (
      <FormPageShell title={isEdit ? "Edit template" : "Add template"}>
        <p className="text-text-secondary">Loading…</p>
      </FormPageShell>
    );
  }
  if (isEdit && detailQuery.error !== null) {
    return (
      <FormPageShell title="Edit template">
        <p role="alert" className="text-danger-fg">
          Couldn't load template: {detailQuery.error.message}
        </p>
      </FormPageShell>
    );
  }

  const cluster =
    isEdit && detailQuery.data !== undefined
      ? clusters.data?.find((c) => c.id === detailQuery.data!.cluster_id)
      : undefined;

  return (
    <FormPageShell title={isEdit ? "Edit template" : "Add template"}>
      {!isEdit && noClusters && (
        <div className={alertBanner}>
          You need at least one cluster before adding a template.{" "}
          <Link to="/admin/clusters/new" className="underline">
            Add a cluster
          </Link>
          .
        </div>
      )}

      <form
        onSubmit={handleSubmit}
        className={
          "bg-surface-1 border border-border-subtle rounded-lg p-6 " +
          "flex flex-col gap-5"
        }
      >
        {submitError !== null && (
          <div role="alert" className={alertBanner}>
            {submitError}
          </div>
        )}

        {/* Cluster: dropdown on create, read-only on edit. */}
        {isEdit ? (
          <FormField label="Cluster" htmlFor="tpl-cluster">
            <input
              id="tpl-cluster"
              type="text"
              readOnly
              disabled
              value={cluster?.name ?? "(unknown)"}
              className={inputClassReadOnly}
            />
          </FormField>
        ) : (
          <FormField label="Cluster" htmlFor="tpl-cluster" required>
            <select
              id="tpl-cluster"
              required
              value={form.cluster_id}
              onChange={(e) =>
                setForm({ ...form, cluster_id: e.target.value })
              }
              className={inputClass}
              disabled={noClusters}
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

        <FormField label="Name" htmlFor="tpl-name" required>
          <input
            id="tpl-name"
            type="text"
            required
            maxLength={256}
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            className={inputClass}
          />
        </FormField>

        <FormField
          label="VMID"
          htmlFor="tpl-vmid"
          required={!isEdit}
          hint={
            isEdit
              ? "VMID is immutable. Destroy and re-register to change."
              : "The Proxmox VMID of the existing template VM."
          }
        >
          <input
            id="tpl-vmid"
            type="number"
            min={1}
            required={!isEdit}
            disabled={isEdit}
            value={form.pve_vmid}
            onChange={(e) => setForm({ ...form, pve_vmid: e.target.value })}
            className={isEdit ? inputClassReadOnly : inputClass}
          />
        </FormField>

        <FormField
          label="Node"
          htmlFor="tpl-node"
          required
          hint="Proxmox node name where the template VM lives."
        >
          <input
            id="tpl-node"
            type="text"
            required
            value={form.pve_node}
            onChange={(e) => setForm({ ...form, pve_node: e.target.value })}
            className={inputClass}
          />
        </FormField>

        <FormField label="OS Type" htmlFor="tpl-os" required>
          <select
            id="tpl-os"
            required
            value={form.os_type}
            onChange={(e) => setForm({ ...form, os_type: e.target.value })}
            className={inputClass}
          >
            {OS_TYPES.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </FormField>

        <FormField label="Description" htmlFor="tpl-description">
          <input
            id="tpl-description"
            type="text"
            maxLength={1024}
            value={form.description}
            onChange={(e) =>
              setForm({ ...form, description: e.target.value })
            }
            className={inputClass}
          />
        </FormField>

        <div className="grid grid-cols-3 gap-4">
          <FormField label="CPU Cores" htmlFor="tpl-cpu" required>
            <input
              id="tpl-cpu"
              type="number"
              min={1}
              required
              value={form.cpu_cores}
              onChange={(e) =>
                setForm({ ...form, cpu_cores: e.target.value })
              }
              className={inputClass}
            />
          </FormField>
          <FormField label="Memory (MB)" htmlFor="tpl-mem" required>
            <input
              id="tpl-mem"
              type="number"
              min={512}
              required
              value={form.memory_mb}
              onChange={(e) =>
                setForm({ ...form, memory_mb: e.target.value })
              }
              className={inputClass}
            />
          </FormField>
          <FormField label="Disk (GB)" htmlFor="tpl-disk" required>
            <input
              id="tpl-disk"
              type="number"
              min={10}
              required
              value={form.disk_gb}
              onChange={(e) =>
                setForm({ ...form, disk_gb: e.target.value })
              }
              className={inputClass}
            />
          </FormField>
        </div>

        <FormField label="GPU required" htmlFor="tpl-gpu">
          <span className="inline-flex items-center gap-2">
            <input
              id="tpl-gpu"
              type="checkbox"
              checked={form.gpu_required}
              onChange={(e) =>
                setForm({ ...form, gpu_required: e.target.checked })
              }
              className="h-4 w-4"
            />
            <span className="text-body-sm text-text-primary">
              Pools using this template require a GPU-capable node
            </span>
          </span>
        </FormField>

        <div className="flex items-center justify-end gap-3 pt-2">
          <Link to="/admin/templates" className={cancelBtn}>
            Cancel
          </Link>
          <button
            type="submit"
            disabled={submitting || (!isEdit && noClusters)}
            className={primaryBtn}
          >
            {submitting
              ? isEdit
                ? "Saving…"
                : "Creating…"
              : isEdit
                ? "Save changes"
                : "Create template"}
          </button>
        </div>
      </form>

      {isEdit && id !== undefined && (
        <ValidatePanel
          isPending={validateMutation.isPending}
          result={validateMutation.data}
          error={validateMutation.error}
          onRun={handleValidate}
        />
      )}
    </FormPageShell>
  );
}


// ── ValidatePanel ──────────────────────────────────────────


interface ValidatePanelProps {
  isPending: boolean;
  result: TemplateValidationResult | undefined;
  error: Error | null;
  onRun: () => void;
}


function ValidatePanel({
  isPending,
  result,
  error,
  onRun,
}: ValidatePanelProps) {
  return (
    <section
      aria-labelledby="validate-heading"
      className={
        "bg-surface-1 border border-border-subtle rounded-lg p-6 " +
        "flex flex-col gap-4"
      }
    >
      <header className="flex items-center justify-between gap-3 flex-wrap">
        <h2
          id="validate-heading"
          className="font-body text-h3 font-semibold text-text-primary"
        >
          Validation
        </h2>
        <button
          type="button"
          onClick={onRun}
          disabled={isPending}
          className={secondaryBtn}
        >
          <Play size={14} strokeWidth={2} aria-hidden />
          {isPending
            ? "Running…"
            : result === undefined
              ? "Run validation"
              : "Re-run validation"}
        </button>
      </header>

      <p className="text-body-sm text-text-secondary -mt-2">
        Checks that the template VM exists, is marked as a template, has
        the guest agent configured, and (if applicable) carries the
        required snapshot. Run after registering or after changes to the
        template VM in Proxmox.
      </p>

      {error !== null && (
        <div role="alert" className={alertBanner}>
          Validation could not run: {error.message}
        </div>
      )}

      {result !== undefined && (
        <div className="flex flex-col gap-3">
          <div
            className={
              "flex items-center gap-2 px-3 py-2 rounded-md text-body-sm " +
              (result.passed
                ? "bg-success-bg border border-success-border text-success-fg"
                : "bg-danger-bg border border-danger-border text-danger-fg")
            }
          >
            {result.passed ? (
              <CheckCircle size={16} strokeWidth={2} aria-hidden />
            ) : (
              <XCircle size={16} strokeWidth={2} aria-hidden />
            )}
            <span className="font-medium">
              {result.passed ? "All checks passed" : "Some checks failed"}
            </span>
          </div>

          <ul className="divide-y divide-border-subtle">
            {result.checks.map((check) => (
              <li
                key={check.name}
                className="py-3 flex items-start gap-3"
              >
                {check.passed ? (
                  <CheckCircle
                    size={16}
                    strokeWidth={2}
                    className="text-success-fg flex-none mt-0.5"
                    aria-hidden
                  />
                ) : (
                  <AlertTriangle
                    size={16}
                    strokeWidth={2}
                    className="text-danger-fg flex-none mt-0.5"
                    aria-hidden
                  />
                )}
                <div className="flex-1 min-w-0">
                  <p className="text-body-sm font-medium text-text-primary">
                    {check.name}
                  </p>
                  <p
                    className={
                      "text-caption mt-0.5 " +
                      (check.passed
                        ? "text-text-secondary"
                        : "text-danger-fg")
                    }
                  >
                    {check.message}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}


// ── Layout shell + class strings ────────────────────────────


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
          <Link to="/admin/templates" className={backLink}>
            <ChevronLeft size={16} aria-hidden />
            <span className="text-body-sm">Back to templates</span>
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


// Style constants — duplicated from ClustersPage for v0.
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
const secondaryBtn =
  "inline-flex items-center gap-2 h-9 px-3 rounded-md " +
  "bg-action-secondary text-action-secondary-text text-body-sm font-medium " +
  "hover:opacity-90 " +
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


function formatSubmitError(exc: unknown): string {
  switch (brokerErrorCode(exc)) {
    case "INVALID_REQUEST":
      return "One or more fields are invalid. Check the values and try again.";
    case "CONFLICT":
      return "A template with this VMID already exists in this cluster.";
    case "NOT_FOUND":
      return "The selected cluster no longer exists.";
    case "PROVIDER_ERROR":
    case "SERVICE_UNAVAILABLE":
      return "Couldn't reach the cluster. Verify the cluster is online.";
    default:
      return "Failed to save. Check broker logs for details.";
  }
}
