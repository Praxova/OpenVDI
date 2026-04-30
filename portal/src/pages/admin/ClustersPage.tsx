import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Pencil, Plus, Trash2 } from "lucide-react";

import {
  useClustersQuery,
  useDeleteClusterMutation,
} from "@/api/admin/clusters";
import { brokerErrorCode } from "@/api/errors";
import {
  DataTable,
  type DataTableColumn,
} from "@/components/admin/DataTable";
import {
  StatusBadge,
  clusterStatusBadge,
} from "@/components/StatusBadge";
import { formatRelativeTime } from "@/lib/time";
import type { ClusterRead } from "@/types/admin";


export function ClustersPage() {
  const navigate = useNavigate();
  const { data, error, isPending, refetch } = useClustersQuery();
  const deleteCluster = useDeleteClusterMutation();
  const [pageError, setPageError] = useState<string | null>(null);

  const handleDelete = async (cluster: ClusterRead) => {
    const ok = window.confirm(
      `Delete cluster "${cluster.name}"? This is irreversible. ` +
        "Pools referencing this cluster must be removed first.",
    );
    if (!ok) return;

    setPageError(null);
    try {
      await deleteCluster.mutateAsync(cluster.id);
    } catch (exc) {
      const code = brokerErrorCode(exc);
      const msg =
        code === "CONFLICT"
          ? `${cluster.name} has pools assigned. Delete those pools first.`
          : code === "NOT_FOUND"
            ? `${cluster.name} no longer exists. Refreshing.`
            : "Failed to delete cluster. See broker logs.";
      setPageError(msg);
      window.setTimeout(() => setPageError(null), 10_000);
    }
  };

  const columns: DataTableColumn<ClusterRead>[] = [
    {
      header: "Name",
      cell: (c) => (
        <span className="font-medium text-text-primary">{c.name}</span>
      ),
    },
    {
      header: "Provider",
      cell: (c) => (
        <span className="text-text-secondary">{c.provider_type}</span>
      ),
    },
    {
      header: "API URL",
      cell: (c) => (
        <span className="text-text-secondary font-mono">
          {c.api_url}
        </span>
      ),
    },
    {
      header: "Status",
      cell: (c) => {
        const badge = clusterStatusBadge(c.status);
        return <StatusBadge tone={badge.tone} label={badge.label} />;
      },
    },
    {
      header: "Updated",
      cell: (c) => (
        <span className="text-text-tertiary whitespace-nowrap">
          {formatRelativeTime(c.updated_at)}
        </span>
      ),
    },
    {
      header: "Actions",
      align: "right",
      cell: (c) => (
        <div className="flex items-center justify-end gap-1">
          <button
            type="button"
            onClick={() => navigate(`/admin/clusters/${c.id}/edit`)}
            aria-label={`Edit ${c.name}`}
            className={
              "inline-flex items-center justify-center h-8 w-8 rounded-md " +
              "text-text-secondary hover:bg-surface-2 hover:text-text-primary " +
              "focus-visible:outline-none focus-visible:shadow-focus"
            }
          >
            <Pencil size={16} aria-hidden />
          </button>
          <button
            type="button"
            onClick={() => handleDelete(c)}
            aria-label={`Delete ${c.name}`}
            disabled={deleteCluster.isPending}
            className={
              "inline-flex items-center justify-center h-8 w-8 rounded-md " +
              "text-text-secondary hover:bg-danger-bg hover:text-danger-fg " +
              "focus-visible:outline-none focus-visible:shadow-focus " +
              "disabled:opacity-50 disabled:cursor-not-allowed"
            }
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
            Clusters
          </h1>
          <p className="text-body text-text-secondary mt-2">
            Hypervisor clusters OpenVDI manages. Add a cluster to register
            its credentials and surface its capacity.
          </p>
        </div>
        <Link
          to="/admin/clusters/new"
          className={
            "inline-flex items-center gap-2 h-10 px-4 rounded-md " +
            "bg-action-primary text-text-on-accent text-body font-medium " +
            "transition-colors duration-fast ease-out " +
            "hover:bg-action-primary-hover " +
            "focus-visible:outline-none focus-visible:shadow-focus"
          }
        >
          <Plus size={16} aria-hidden />
          Add cluster
        </Link>
      </header>

      <div className="max-w-6xl mx-auto">
        {pageError !== null && (
          <div
            role="alert"
            className={
              "mb-4 px-4 py-3 rounded-md " +
              "bg-danger-bg border border-danger-border text-danger-fg " +
              "text-body-sm"
            }
          >
            {pageError}
          </div>
        )}
        <DataTable
          columns={columns}
          data={data}
          isPending={isPending}
          error={error}
          onRetry={() => refetch()}
          rowKey={(c) => c.id}
          emptyMessage="No clusters registered yet. Click 'Add cluster' to get started."
        />
      </div>
    </div>
  );
}
