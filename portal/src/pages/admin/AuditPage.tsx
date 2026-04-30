import {
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import {
  useAuditQuery,
  type AuditListFilters,
} from "@/api/admin/audit";
import { presetToSince, type TimePreset } from "@/api/admin/sessions";
import {
  DataTable,
  type DataTableColumn,
} from "@/components/admin/DataTable";
import { formatRelativeTime } from "@/lib/time";
import type { AuditRead } from "@/types/admin";

import { AuditDetailDrawer } from "./AuditDetailDrawer";


const RESOURCE_TYPES = [
  "cluster",
  "template",
  "pool",
  "desktop",
  "session",
  "entitlement",
  "auth",
] as const;

const UUID_PATTERN =
  /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;

const LIMIT = 50;


export function AuditPage() {
  const [actorFilter, setActorFilter] = useState<string>("");
  const [actionFilter, setActionFilter] = useState<string>("");
  const [resourceTypeFilter, setResourceTypeFilter] = useState<string>("");
  const [resourceIdFilter, setResourceIdFilter] = useState<string>("");
  const [timePreset, setTimePreset] = useState<TimePreset>("24h");
  const [offset, setOffset] = useState(0);
  const [selectedRow, setSelectedRow] = useState<AuditRead | null>(null);

  const trimmedResourceId = resourceIdFilter.trim();
  const isResourceIdValid =
    trimmedResourceId === "" || UUID_PATTERN.test(trimmedResourceId);

  // Filter changes reset to page 1.
  useEffect(() => {
    setOffset(0);
  }, [
    actorFilter,
    actionFilter,
    resourceTypeFilter,
    resourceIdFilter,
    timePreset,
  ]);

  const filters: AuditListFilters = useMemo(
    () => ({
      actor: actorFilter.trim() || undefined,
      action: actionFilter.trim() || undefined,
      resource_type: resourceTypeFilter || undefined,
      resource_id:
        isResourceIdValid && trimmedResourceId
          ? trimmedResourceId
          : undefined,
      since: presetToSince(timePreset),
      offset,
      limit: LIMIT,
    }),
    [
      actorFilter,
      actionFilter,
      resourceTypeFilter,
      trimmedResourceId,
      isResourceIdValid,
      timePreset,
      offset,
    ],
  );

  const audit = useAuditQuery(filters);

  const hasPrev = offset > 0;
  const hasNext = audit.data?.length === LIMIT;

  const columns: DataTableColumn<AuditRead>[] = [
    {
      header: "Time",
      cell: (r) => (
        <span
          className="text-text-tertiary whitespace-nowrap"
          title={r.timestamp}
        >
          {formatRelativeTime(r.timestamp)}
        </span>
      ),
    },
    {
      header: "Actor",
      cell: (r) =>
        r.actor !== null ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setActorFilter(r.actor as string);
            }}
            className="font-mono text-text-primary hover:underline"
            title="Filter by this actor"
          >
            {r.actor}
          </button>
        ) : (
          <span className="text-text-tertiary italic">system</span>
        ),
    },
    {
      header: "Action",
      cell: (r) => (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setActionFilter(r.action);
          }}
          className="font-mono text-text-secondary hover:underline"
          title="Filter by this action"
        >
          {r.action}
        </button>
      ),
    },
    {
      header: "Resource",
      cell: (r) =>
        r.resource_type !== null ? (
          <span className="text-text-secondary">
            {r.resource_type}
            {r.resource_id !== null && (
              <span className="text-text-tertiary text-caption font-mono ml-1">
                · {r.resource_id.slice(0, 8)}
              </span>
            )}
          </span>
        ) : (
          <span className="text-text-tertiary">—</span>
        ),
    },
    {
      header: "Client IP",
      cell: (r) => (
        <span className="font-mono text-text-tertiary">
          {r.client_ip ?? "—"}
        </span>
      ),
    },
  ];

  return (
    <div className="px-6 py-8">
      <header className="max-w-6xl mx-auto mb-6">
        <h1 className="font-display text-h1 font-semibold text-text-primary">
          Audit
        </h1>
        <p className="text-body text-text-secondary mt-2">
          Append-only record of every business action on the broker.
          Default retention is 90 days (configurable via
          OPENVDI_AUDIT_RETENTION_DAYS).
        </p>
      </header>

      <div className="max-w-6xl mx-auto">
        <div className="mb-4 flex flex-wrap gap-3 items-end">
          <FilterField label="Actor" htmlFor="filter-actor">
            <input
              id="filter-actor"
              type="text"
              placeholder="username or service id"
              value={actorFilter}
              onChange={(e) => setActorFilter(e.target.value)}
              className={filterInput}
            />
          </FilterField>
          <FilterField label="Action" htmlFor="filter-action">
            <input
              id="filter-action"
              type="text"
              placeholder="exact, or trailing * for prefix"
              value={actionFilter}
              onChange={(e) => setActionFilter(e.target.value)}
              className={filterInput}
            />
          </FilterField>
          <FilterField label="Resource type" htmlFor="filter-rtype">
            <select
              id="filter-rtype"
              value={resourceTypeFilter}
              onChange={(e) => setResourceTypeFilter(e.target.value)}
              className={filterInput}
            >
              <option value="">All types</option>
              {RESOURCE_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </FilterField>
          <FilterField label="Resource ID" htmlFor="filter-rid">
            <input
              id="filter-rid"
              type="text"
              placeholder="UUID"
              value={resourceIdFilter}
              onChange={(e) => setResourceIdFilter(e.target.value)}
              aria-invalid={!isResourceIdValid}
              className={
                isResourceIdValid ? filterInput : filterInputInvalid
              }
            />
          </FilterField>
          <FilterField label="Time range" htmlFor="filter-time">
            <select
              id="filter-time"
              value={timePreset}
              onChange={(e) => setTimePreset(e.target.value as TimePreset)}
              className={filterInput}
            >
              <option value="24h">Last 24 hours</option>
              <option value="7d">Last 7 days</option>
              <option value="30d">Last 30 days</option>
              <option value="all">All time</option>
            </select>
          </FilterField>
        </div>

        <DataTable
          columns={columns}
          data={audit.data}
          isPending={audit.isPending}
          error={audit.error}
          onRetry={() => audit.refetch()}
          rowKey={(r) => String(r.id)}
          onRowClick={(r) => setSelectedRow(r)}
          emptyMessage="No audit rows match the current filters."
        />

        <div className="mt-3 flex items-center justify-between">
          <span className="text-caption text-text-tertiary">
            {audit.data !== undefined &&
              (audit.data.length === 0
                ? "No rows"
                : `Showing rows ${offset + 1}–${
                    offset + audit.data.length
                  }`)}
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setOffset(Math.max(0, offset - LIMIT))}
              disabled={!hasPrev || audit.isPending}
              aria-label="Previous page"
              className={paginationBtn}
            >
              <ChevronLeft size={14} aria-hidden /> Prev
            </button>
            <button
              type="button"
              onClick={() => setOffset(offset + LIMIT)}
              disabled={!hasNext || audit.isPending}
              aria-label="Next page"
              className={paginationBtn}
            >
              Next <ChevronRight size={14} aria-hidden />
            </button>
          </div>
        </div>
      </div>

      <AuditDetailDrawer
        row={selectedRow}
        onClose={() => setSelectedRow(null)}
      />
    </div>
  );
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
const filterInputInvalid =
  "h-10 px-3 rounded-md border border-danger-border bg-surface-1 " +
  "text-body-sm text-text-primary " +
  "focus-visible:outline-none focus-visible:shadow-focus";
const paginationBtn =
  "inline-flex items-center gap-1 h-9 px-3 rounded-md " +
  "border border-border-subtle bg-surface-1 " +
  "text-body-sm text-text-primary " +
  "hover:bg-surface-2 " +
  "focus-visible:outline-none focus-visible:shadow-focus " +
  "disabled:opacity-50 disabled:cursor-not-allowed";
