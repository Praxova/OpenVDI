import { useMemo, useState, type ReactNode } from "react";
import { Power } from "lucide-react";

import { usePoolsQuery } from "@/api/admin/pools";
import {
  presetToSince,
  useForceDisconnectMutation,
  useSessionsQuery,
  type SessionListFilters,
  type TimePreset,
} from "@/api/admin/sessions";
import { brokerErrorCode } from "@/api/errors";
import {
  DataTable,
  type DataTableColumn,
} from "@/components/admin/DataTable";
import {
  StatusBadge,
  sessionStatusBadge,
} from "@/components/StatusBadge";
import { formatRelativeTime } from "@/lib/time";
import type {
  PoolRead,
  SessionReadAdmin,
  SessionStatus,
} from "@/types/admin";

import { SessionDetailDrawer } from "./SessionDetailDrawer";


const SESSION_STATUSES: SessionStatus[] = [
  "connecting",
  "active",
  "disconnected",
  "ended",
];


export function SessionsPage() {
  const [userFilter, setUserFilter] = useState<string>("");
  const [poolFilter, setPoolFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<SessionStatus | "">("");
  const [includeEnded, setIncludeEnded] = useState<boolean>(false);
  const [timePreset, setTimePreset] = useState<TimePreset>("24h");
  const [pageError, setPageError] = useState<string | null>(null);
  const [pageSuccess, setPageSuccess] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Memoize filters on the primitive inputs only — calling
  // presetToSince inside this useMemo means Date.now() is sampled
  // exactly once per filter change, not per render.
  const filters: SessionListFilters = useMemo(
    () => ({
      username: userFilter.trim() || undefined,
      pool_id: poolFilter || undefined,
      status: (statusFilter || undefined) as SessionStatus | undefined,
      since: includeEnded ? presetToSince(timePreset) : undefined,
      include_ended: includeEnded,
    }),
    [userFilter, poolFilter, statusFilter, includeEnded, timePreset],
  );

  const sessions = useSessionsQuery(filters);
  const pools = usePoolsQuery();
  const disconnect = useForceDisconnectMutation();

  const poolMap = new Map<string, PoolRead>();
  if (pools.data !== undefined) {
    for (const p of pools.data) poolMap.set(p.id, p);
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

  const handleForceDisconnect = async (s: SessionReadAdmin) => {
    // Per FE7: no confirm dialog. The button label + position +
    // success banner naming the user are the safety net.
    try {
      await disconnect.mutateAsync(s.id);
      flashSuccess(`${s.username}'s session ended.`);
    } catch (exc) {
      flashError(
        `Failed to disconnect ${s.username}: ${formatDisconnectError(exc)}`,
      );
    }
  };

  const columns: DataTableColumn<SessionReadAdmin>[] = [
    {
      header: "User",
      cell: (s) => (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setUserFilter(s.username);
          }}
          className="font-mono text-text-primary hover:underline"
          title="Filter by this user"
        >
          {s.username}
        </button>
      ),
    },
    {
      header: "Desktop",
      cell: (s) =>
        s.desktop_name !== null ? (
          <span className="text-text-secondary">{s.desktop_name}</span>
        ) : (
          <span className="text-text-tertiary italic">
            (desktop deleted)
          </span>
        ),
    },
    {
      header: "Pool",
      cell: (s) =>
        s.pool_name !== null ? (
          <span className="text-text-secondary">{s.pool_name}</span>
        ) : (
          <span className="text-text-tertiary italic">(pool deleted)</span>
        ),
    },
    {
      header: "Status",
      cell: (s) => <StatusBadge {...sessionStatusBadge(s.status)} />,
    },
    {
      header: "Connected",
      cell: (s) => (
        <span className="text-text-tertiary whitespace-nowrap">
          {s.connected_at !== null
            ? formatRelativeTime(s.connected_at)
            : "—"}
        </span>
      ),
    },
    {
      header: "Client IP",
      cell: (s) => (
        <span className="font-mono text-text-tertiary">
          {s.client_ip ?? "—"}
        </span>
      ),
    },
    {
      header: "Actions",
      align: "right",
      cell: (s) => (
        <div
          className="flex items-center justify-end gap-1"
          onClick={(e) => e.stopPropagation()}
        >
          {canForceDisconnect(s.status) && (
            <button
              type="button"
              onClick={() => handleForceDisconnect(s)}
              aria-label={`Force disconnect ${s.username}`}
              title="Force disconnect"
              disabled={disconnect.isPending}
              className={forceDisconnectBtn}
            >
              <Power size={14} aria-hidden />
              <span>Force disconnect</span>
            </button>
          )}
        </div>
      ),
    },
  ];

  return (
    <div className="px-6 py-8">
      <header className="max-w-6xl mx-auto mb-6">
        <h1 className="font-display text-h1 font-semibold text-text-primary">
          Sessions
        </h1>
        <p className="text-body text-text-secondary mt-2">
          Live and historical user sessions. Force-disconnect cuts the
          noVNC tunnel; the desktop's VM keeps running and the user can
          reconnect.
        </p>
      </header>

      <div className="max-w-6xl mx-auto">
        <div className="mb-4 flex flex-wrap gap-3 items-end">
          <FilterField label="User" htmlFor="filter-user">
            <input
              id="filter-user"
              type="text"
              placeholder="AD username"
              value={userFilter}
              onChange={(e) => setUserFilter(e.target.value)}
              className={filterInput}
            />
          </FilterField>
          <FilterField label="Pool" htmlFor="filter-pool">
            <select
              id="filter-pool"
              value={poolFilter}
              onChange={(e) => setPoolFilter(e.target.value)}
              className={filterInput}
            >
              <option value="">All pools</option>
              {pools.data?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.display_name}
                </option>
              ))}
            </select>
          </FilterField>
          <FilterField label="Status" htmlFor="filter-status">
            <select
              id="filter-status"
              value={statusFilter}
              onChange={(e) =>
                setStatusFilter(e.target.value as SessionStatus | "")
              }
              className={filterInput}
            >
              <option value="">All statuses</option>
              {SESSION_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {sessionStatusBadge(s).label}
                </option>
              ))}
            </select>
          </FilterField>
          <FilterField label="Time range" htmlFor="filter-time">
            <select
              id="filter-time"
              value={timePreset}
              onChange={(e) => setTimePreset(e.target.value as TimePreset)}
              disabled={!includeEnded}
              className={filterInput}
            >
              <option value="24h">Last 24 hours</option>
              <option value="7d">Last 7 days</option>
              <option value="30d">Last 30 days</option>
              <option value="all">All time</option>
            </select>
          </FilterField>
          <label className="flex items-center gap-2 text-body-sm text-text-secondary pb-2">
            <input
              type="checkbox"
              checked={includeEnded}
              onChange={(e) => setIncludeEnded(e.target.checked)}
            />
            Include ended/disconnected
          </label>
        </div>

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
          data={sessions.data}
          isPending={sessions.isPending}
          error={sessions.error}
          onRetry={() => sessions.refetch()}
          rowKey={(s) => s.id}
          onRowClick={(s) => setSelectedId(s.id)}
          emptyMessage={
            includeEnded
              ? "No sessions match the current filters in the selected range."
              : "No active sessions. Toggle 'Include ended/disconnected' to see history."
          }
        />

        {sessions.data?.length === 50 && (
          <p className="mt-3 text-caption text-text-tertiary">
            Showing 50 sessions. Pagination is M5+; narrow filters or
            shrink the time range to find a specific session.
          </p>
        )}
      </div>

      <SessionDetailDrawer
        sessionId={selectedId}
        onClose={() => setSelectedId(null)}
      />
    </div>
  );
}


// ── Visibility predicates + error formatters ─────────────────


export function canForceDisconnect(status: SessionStatus): boolean {
  return status === "connecting" || status === "active";
}


function formatDisconnectError(exc: unknown): string {
  switch (brokerErrorCode(exc)) {
    case "NOT_FOUND":
      return "Session no longer exists.";
    default:
      return "Check broker logs.";
  }
}


// ── Filter field wrapper ───────────────────────────────────


function FilterField({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: ReactNode;
}) {
  return (
    <label htmlFor={htmlFor} className="flex flex-col gap-1">
      <span className="text-caption uppercase tracking-wide text-text-tertiary font-medium">
        {label}
      </span>
      {children}
    </label>
  );
}


// ── Style constants (duplicated from sibling pages) ────────


const filterInput =
  "h-10 px-3 rounded-md border border-border-default bg-surface-1 " +
  "text-body-sm text-text-primary " +
  "focus-visible:outline-none focus-visible:shadow-focus";
const forceDisconnectBtn =
  "inline-flex items-center gap-1.5 h-8 px-3 rounded-md " +
  "border border-danger-border bg-danger-bg text-danger-fg " +
  "text-body-sm font-medium " +
  "hover:opacity-90 " +
  "focus-visible:outline-none focus-visible:shadow-focus " +
  "disabled:opacity-50 disabled:cursor-not-allowed";
const alertBanner =
  "mb-4 px-4 py-3 rounded-md " +
  "bg-danger-bg border border-danger-border text-danger-fg " +
  "text-body-sm";
const successBanner =
  "mb-4 px-4 py-3 rounded-md " +
  "bg-success-bg border border-success-border text-success-fg " +
  "text-body-sm";
