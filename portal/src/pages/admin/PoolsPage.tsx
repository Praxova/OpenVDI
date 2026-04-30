import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Pencil, Play, Plus, Square, Trash2 } from "lucide-react";

import { useClustersQuery } from "@/api/admin/clusters";
import { useCapacityQuery } from "@/api/admin/dashboard";
import {
  useDeletePoolMutation,
  useDrainPoolMutation,
  usePoolsQuery,
  useProvisionPoolMutation,
} from "@/api/admin/pools";
import { brokerErrorCode } from "@/api/errors";
import {
  DataTable,
  type DataTableColumn,
} from "@/components/admin/DataTable";
import {
  StatusBadge,
  poolStatusBadge,
  poolTypeBadge,
} from "@/components/StatusBadge";
import { formatRelativeTime } from "@/lib/time";
import type {
  ClusterRead,
  PoolCapacityRow,
  PoolRead,
} from "@/types/admin";


export function PoolsPage() {
  const navigate = useNavigate();
  const pools = usePoolsQuery();
  const capacity = useCapacityQuery();
  const clusters = useClustersQuery();
  const deleteMutation = useDeletePoolMutation();
  const provisionMutation = useProvisionPoolMutation();
  const drainMutation = useDrainPoolMutation();
  const [pageError, setPageError] = useState<string | null>(null);
  const [pageSuccess, setPageSuccess] = useState<string | null>(null);

  const clusterMap = new Map<string, ClusterRead>();
  if (clusters.data !== undefined) {
    for (const c of clusters.data) clusterMap.set(c.id, c);
  }
  const capacityMap = new Map<string, PoolCapacityRow>();
  if (capacity.data !== undefined) {
    for (const c of capacity.data) capacityMap.set(c.pool_id, c);
  }

  const flashError = (msg: string) => {
    setPageError(msg);
    setPageSuccess(null);
    window.setTimeout(() => setPageError(null), 10_000);
  };
  const flashSuccess = (msg: string) => {
    setPageSuccess(msg);
    setPageError(null);
    window.setTimeout(() => setPageSuccess(null), 5_000);
  };

  const handleProvision = async (pool: PoolRead) => {
    // The broker requires a count (1..50). Default to min_spare; admin
    // can override. window.prompt is the v0 input mechanism per the
    // sibling pattern.
    const defaultCount = String(pool.min_spare > 0 ? pool.min_spare : 1);
    const input = window.prompt(
      `How many additional desktops to provision in ` +
        `"${pool.display_name}"? (1-50)`,
      defaultCount,
    );
    if (input === null) return;
    const count = parseInt(input, 10);
    if (Number.isNaN(count) || count < 1 || count > 50) {
      flashError(`Invalid count: ${input}. Must be between 1 and 50.`);
      return;
    }
    try {
      await provisionMutation.mutateAsync({ id: pool.id, count });
      flashSuccess(
        `${pool.display_name}: provisioning ${count} desktop(s).`,
      );
    } catch (exc) {
      flashError(`${pool.display_name}: ${formatProvisionError(exc)}`);
    }
  };

  const handleDrain = async (pool: PoolRead) => {
    const ok = window.confirm(
      `Drain pool "${pool.display_name}"? Active sessions will end as ` +
        "users disconnect; new connects will be refused.",
    );
    if (!ok) return;
    try {
      await drainMutation.mutateAsync(pool.id);
      flashSuccess(`${pool.display_name}: draining.`);
    } catch (exc) {
      flashError(`${pool.display_name}: ${formatDrainError(exc)}`);
    }
  };

  const handleDelete = async (pool: PoolRead) => {
    const ok = window.confirm(
      `Delete pool "${pool.display_name}"? This destroys all unassigned ` +
        "desktops and the pool definition. Pools with active sessions cannot " +
        "be deleted — drain first.",
    );
    if (!ok) return;
    try {
      await deleteMutation.mutateAsync(pool.id);
      flashSuccess(`${pool.display_name}: deletion accepted.`);
    } catch (exc) {
      flashError(`${pool.display_name}: ${formatDeleteError(exc)}`);
    }
  };

  const columns: DataTableColumn<PoolRead>[] = [
    {
      header: "Pool",
      cell: (p) => (
        <div>
          <div className="font-medium text-text-primary">
            {p.display_name}
          </div>
          <div className="text-text-tertiary text-caption font-mono">
            {p.name}
          </div>
        </div>
      ),
    },
    {
      header: "Type",
      cell: (p) => {
        const badge = poolTypeBadge(p.pool_type);
        return <StatusBadge tone={badge.tone} label={badge.label} />;
      },
    },
    {
      header: "Cluster",
      cell: (p) => {
        const c = clusterMap.get(p.cluster_id);
        return (
          <span className="text-text-secondary">
            {c?.name ?? p.cluster_id.slice(0, 8)}
          </span>
        );
      },
    },
    {
      header: "Capacity",
      cell: (p) => {
        const cap = capacityMap.get(p.id);
        const available = cap !== undefined ? String(cap.available) : "—";
        return (
          <span className="font-mono text-text-secondary">
            {available}/{p.max_size}
          </span>
        );
      },
    },
    {
      header: "Status",
      cell: (p) => {
        const badge = poolStatusBadge(p.status);
        return <StatusBadge tone={badge.tone} label={badge.label} />;
      },
    },
    {
      header: "Updated",
      cell: (p) => (
        <span className="text-text-tertiary whitespace-nowrap">
          {formatRelativeTime(p.updated_at)}
        </span>
      ),
    },
    {
      header: "Actions",
      align: "right",
      cell: (p) => (
        <div className="flex items-center justify-end gap-1">
          <button
            type="button"
            onClick={() => navigate(`/admin/pools/${p.id}/edit`)}
            aria-label={`Edit ${p.display_name}`}
            className={iconBtn}
          >
            <Pencil size={16} aria-hidden />
          </button>
          {p.pool_type === "nonpersistent" && p.status === "active" && (
            <button
              type="button"
              onClick={() => handleProvision(p)}
              aria-label={`Provision warm spares for ${p.display_name}`}
              title="Provision warm spares"
              disabled={provisionMutation.isPending}
              className={iconBtn}
            >
              <Play size={16} aria-hidden />
            </button>
          )}
          {p.status === "active" && (
            <button
              type="button"
              onClick={() => handleDrain(p)}
              aria-label={`Drain ${p.display_name}`}
              title="Drain pool"
              disabled={drainMutation.isPending}
              className={iconBtn}
            >
              <Square size={16} aria-hidden />
            </button>
          )}
          <button
            type="button"
            onClick={() => handleDelete(p)}
            aria-label={`Delete ${p.display_name}`}
            disabled={deleteMutation.isPending}
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
            Pools
          </h1>
          <p className="text-body text-text-secondary mt-2">
            Desktop pools — collections of cloned VMs that users connect
            to. Manage capacity, placement, and entitlements.
          </p>
        </div>
        <Link to="/admin/pools/new" className={primaryBtn}>
          <Plus size={16} aria-hidden />
          Add pool
        </Link>
      </header>

      <div className="max-w-6xl mx-auto">
        {pageError !== null && (
          <div role="alert" className={alertBanner}>
            {pageError}
          </div>
        )}
        {pageSuccess !== null && (
          <div role="status" className={successBanner}>
            {pageSuccess}
          </div>
        )}
        <DataTable
          columns={columns}
          data={pools.data}
          isPending={pools.isPending}
          error={pools.error}
          onRetry={() => pools.refetch()}
          rowKey={(p) => p.id}
          emptyMessage="No pools yet. Click 'Add pool' to create one."
        />
      </div>
    </div>
  );
}


function formatProvisionError(exc: unknown): string {
  switch (brokerErrorCode(exc)) {
    case "CONFLICT":
      return "Pool isn't accepting provisioning right now.";
    case "SERVICE_UNAVAILABLE":
      return "Cluster is offline.";
    case "INVALID_REQUEST":
      return "Invalid count or pool state.";
    default:
      return "Provisioning failed. Check broker logs.";
  }
}

function formatDrainError(exc: unknown): string {
  switch (brokerErrorCode(exc)) {
    case "CONFLICT":
      return "Pool already draining or in a non-draining state.";
    default:
      return "Drain failed. Check broker logs.";
  }
}

function formatDeleteError(exc: unknown): string {
  switch (brokerErrorCode(exc)) {
    case "CONFLICT":
      return "Active sessions exist. Drain the pool first, then retry.";
    default:
      return "Delete failed. Check broker logs.";
  }
}


// Style constants — duplicated from sibling pages.
const iconBtn =
  "inline-flex items-center justify-center h-8 w-8 rounded-md " +
  "text-text-secondary hover:bg-surface-2 hover:text-text-primary " +
  "focus-visible:outline-none focus-visible:shadow-focus " +
  "disabled:opacity-50 disabled:cursor-not-allowed";
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
const successBanner =
  "mb-4 px-4 py-3 rounded-md " +
  "bg-success-bg border border-success-border text-success-fg " +
  "text-body-sm";
