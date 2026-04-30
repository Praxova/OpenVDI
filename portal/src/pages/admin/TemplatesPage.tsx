import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Pencil, Plus, Trash2 } from "lucide-react";

import { useClustersQuery } from "@/api/admin/clusters";
import {
  useDeleteTemplateMutation,
  useTemplatesQuery,
} from "@/api/admin/templates";
import { brokerErrorCode } from "@/api/errors";
import {
  DataTable,
  type DataTableColumn,
} from "@/components/admin/DataTable";
import { formatRelativeTime } from "@/lib/time";
import type { ClusterRead, TemplateRead } from "@/types/admin";


export function TemplatesPage() {
  const navigate = useNavigate();
  const templates = useTemplatesQuery();
  const clusters = useClustersQuery();
  const deleteTemplate = useDeleteTemplateMutation();
  const [pageError, setPageError] = useState<string | null>(null);

  // Build a cluster-id-to-name lookup. If clusters fails, fall back
  // to UUID prefix.
  const clusterMap = new Map<string, ClusterRead>();
  if (clusters.data !== undefined) {
    for (const c of clusters.data) clusterMap.set(c.id, c);
  }

  const handleDelete = async (template: TemplateRead) => {
    const ok = window.confirm(
      `Delete template "${template.name}"? This is irreversible. ` +
        "Pools using this template must be removed first.",
    );
    if (!ok) return;

    setPageError(null);
    try {
      await deleteTemplate.mutateAsync(template.id);
    } catch (exc) {
      const code = brokerErrorCode(exc);
      const msg =
        code === "CONFLICT"
          ? `${template.name} is referenced by one or more pools. ` +
            "Delete those pools first."
          : code === "NOT_FOUND"
            ? `${template.name} no longer exists. Refreshing.`
            : "Failed to delete template. See broker logs.";
      setPageError(msg);
      window.setTimeout(() => setPageError(null), 10_000);
    }
  };

  const columns: DataTableColumn<TemplateRead>[] = [
    {
      header: "Name",
      cell: (t) => (
        <span className="font-medium text-text-primary">{t.name}</span>
      ),
    },
    {
      header: "Cluster",
      cell: (t) => {
        const cluster = clusterMap.get(t.cluster_id);
        return (
          <span className="text-text-secondary">
            {cluster?.name ?? t.cluster_id.slice(0, 8)}
          </span>
        );
      },
    },
    {
      header: "VMID",
      cell: (t) => (
        <span className="font-mono text-text-secondary">{t.pve_vmid}</span>
      ),
    },
    {
      header: "Node",
      cell: (t) => (
        <span className="text-text-secondary">{t.pve_node}</span>
      ),
    },
    {
      header: "OS",
      cell: (t) => (
        <span className="text-text-secondary">{t.os_type}</span>
      ),
    },
    {
      header: "Updated",
      cell: (t) => (
        <span className="text-text-tertiary whitespace-nowrap">
          {formatRelativeTime(t.updated_at)}
        </span>
      ),
    },
    {
      header: "Actions",
      align: "right",
      cell: (t) => (
        <div className="flex items-center justify-end gap-1">
          <button
            type="button"
            onClick={() => navigate(`/admin/templates/${t.id}/edit`)}
            aria-label={`Edit ${t.name}`}
            className={iconBtn}
          >
            <Pencil size={16} aria-hidden />
          </button>
          <button
            type="button"
            onClick={() => handleDelete(t)}
            aria-label={`Delete ${t.name}`}
            disabled={deleteTemplate.isPending}
            className={iconBtnDanger}
          >
            <Trash2 size={16} aria-hidden />
          </button>
        </div>
      ),
    },
  ];

  return (
    <div className="px-6 py-8">
      <header className="max-w-6xl mx-auto mb-6 flex items-end justify-between gap-4 flex-wrap">
        <div>
          <h1 className="font-display text-h1 font-semibold text-text-primary">
            Templates
          </h1>
          <p className="text-body text-text-secondary mt-2">
            Golden VM images that pools clone from. Register an existing
            Proxmox template here.
          </p>
        </div>
        <Link to="/admin/templates/new" className={primaryBtn}>
          <Plus size={16} aria-hidden />
          Add template
        </Link>
      </header>

      <div className="max-w-6xl mx-auto">
        {pageError !== null && (
          <div role="alert" className={alertBanner}>
            {pageError}
          </div>
        )}
        <DataTable
          columns={columns}
          data={templates.data}
          isPending={templates.isPending}
          error={templates.error}
          onRetry={() => templates.refetch()}
          rowKey={(t) => t.id}
          emptyMessage="No templates registered yet. Click 'Add template' to get started."
        />
      </div>
    </div>
  );
}


// Style constants — duplicated from ClustersPage. M5+ may extract.
const iconBtn =
  "inline-flex items-center justify-center h-8 w-8 rounded-md " +
  "text-text-secondary hover:bg-surface-2 hover:text-text-primary " +
  "focus-visible:outline-none focus-visible:shadow-focus";
const iconBtnDanger =
  "inline-flex items-center justify-center h-8 w-8 rounded-md " +
  "text-text-secondary hover:bg-danger-bg hover:text-danger-fg " +
  "focus-visible:outline-none focus-visible:shadow-focus " +
  "disabled:opacity-50 disabled:cursor-not-allowed";
const primaryBtn =
  "inline-flex items-center gap-2 h-10 px-4 rounded-md " +
  "bg-action-primary text-text-on-accent text-body font-medium " +
  "transition-colors duration-fast ease-out " +
  "hover:bg-action-primary-hover " +
  "focus-visible:outline-none focus-visible:shadow-focus";
const alertBanner =
  "mb-4 px-4 py-3 rounded-md " +
  "bg-danger-bg border border-danger-border text-danger-fg " +
  "text-body-sm";
