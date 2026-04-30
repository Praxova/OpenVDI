import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  ChevronDown,
  MinusCircle,
  Power,
  RotateCcw,
  Trash2,
  UserPlus,
} from "lucide-react";

import {
  useAssignDesktopMutation,
  useDesktopPowerMutation,
  useDesktopsQuery,
  useDestroyDesktopMutation,
  useRebuildDesktopMutation,
  useUnassignDesktopMutation,
} from "@/api/admin/desktops";
import { usePoolsQuery } from "@/api/admin/pools";
import { brokerErrorCode } from "@/api/errors";
import {
  DataTable,
  type DataTableColumn,
} from "@/components/admin/DataTable";
import {
  StatusBadge,
  desktopStatusBadge,
} from "@/components/StatusBadge";
import { formatRelativeTime } from "@/lib/time";
import type {
  DesktopRead,
  DesktopStatus,
  PoolRead,
  PowerAction,
} from "@/types/admin";

import { DesktopDetailDrawer } from "./DesktopDetailDrawer";


const DESKTOP_STATUSES: DesktopStatus[] = [
  "provisioning",
  "available",
  "assigned",
  "connected",
  "disconnected",
  "error",
  "deleting",
  "maintenance",
];


export function DesktopsPage() {
  const [poolFilter, setPoolFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<DesktopStatus | "">("");
  const [userFilter, setUserFilter] = useState<string>("");
  const [pageError, setPageError] = useState<string | null>(null);
  const [pageSuccess, setPageSuccess] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);

  const filters = useMemo(
    () => ({
      pool_id: poolFilter || undefined,
      status: (statusFilter || undefined) as DesktopStatus | undefined,
      assigned_user: userFilter.trim() || undefined,
    }),
    [poolFilter, statusFilter, userFilter],
  );

  const desktops = useDesktopsQuery(filters);
  const pools = usePoolsQuery();

  const assign = useAssignDesktopMutation();
  const unassign = useUnassignDesktopMutation();
  const power = useDesktopPowerMutation();
  const rebuild = useRebuildDesktopMutation();
  const destroy = useDestroyDesktopMutation();

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

  const handleAssign = async (d: DesktopRead) => {
    const username = window.prompt(
      `Assign desktop "${d.name}" to which AD user?`,
    );
    if (username === null || username.trim() === "") return;
    const trimmed = username.trim();
    try {
      await assign.mutateAsync({ id: d.id, data: { username: trimmed } });
      flashSuccess(`${d.name}: assigned to ${trimmed}.`);
    } catch (exc) {
      flashError(`${d.name}: ${formatAssignError(exc, trimmed)}`);
    }
  };

  const handleUnassign = async (d: DesktopRead) => {
    try {
      await unassign.mutateAsync(d.id);
      flashSuccess(`${d.name}: unassigned.`);
    } catch (exc) {
      flashError(`${d.name}: ${formatUnassignError(exc)}`);
    }
  };

  const handlePower = async (d: DesktopRead, action: PowerAction) => {
    setOpenMenuId(null);
    if (!window.confirm(powerConfirmText(d.name, action))) return;
    try {
      await power.mutateAsync({ id: d.id, action });
      flashSuccess(
        `${d.name}: ${action} accepted; refresh in a few seconds.`,
      );
    } catch (exc) {
      flashError(`${d.name}: ${formatPowerError(exc, action)}`);
    }
  };

  const handleRebuild = async (d: DesktopRead) => {
    const ok = window.confirm(
      `Rebuild "${d.name}"? The desktop is destroyed and re-cloned from ` +
        "the template. Any user data NOT on a roaming profile is lost. " +
        "Cannot be undone.",
    );
    if (!ok) return;
    try {
      await rebuild.mutateAsync(d.id);
      flashSuccess(`${d.name}: rebuild kicked off.`);
    } catch (exc) {
      flashError(`${d.name}: ${formatRebuildError(exc)}`);
    }
  };

  const handleDestroy = async (d: DesktopRead) => {
    const ok = window.confirm(
      `Destroy "${d.name}"? The VM and its disks are deleted. The pool ` +
        "provisioner will create a replacement only if the pool's " +
        "min_spare or max_size warrants it. Cannot be undone.",
    );
    if (!ok) return;
    try {
      await destroy.mutateAsync(d.id);
      flashSuccess(`${d.name}: destroy kicked off.`);
    } catch (exc) {
      flashError(`${d.name}: ${formatDestroyError(exc)}`);
    }
  };

  const columns: DataTableColumn<DesktopRead>[] = [
    {
      header: "Desktop",
      cell: (d) => (
        <div className="min-w-0">
          <div className="font-medium text-text-primary truncate">
            {d.name}
          </div>
          <div className="text-text-tertiary text-caption font-mono">
            {d.pve_node} · vmid {d.pve_vmid}
          </div>
        </div>
      ),
    },
    {
      header: "Pool",
      cell: (d) => {
        const p = poolMap.get(d.pool_id);
        return (
          <span className="text-text-secondary">
            {p?.display_name ?? d.pool_id.slice(0, 8)}
          </span>
        );
      },
    },
    {
      header: "Assigned",
      cell: (d) =>
        d.assigned_user !== null ? (
          <span className="font-mono text-text-primary">
            {d.assigned_user}
          </span>
        ) : (
          <span className="text-text-tertiary">—</span>
        ),
    },
    {
      header: "Status",
      cell: (d) => {
        const badge = desktopStatusBadge(d.status);
        const drift = !powerStateMatchesStatus(d.status, d.power_state);
        return (
          <div className="flex items-center gap-2">
            <StatusBadge tone={badge.tone} label={badge.label} />
            {drift && (
              <span className="text-caption text-text-tertiary">
                ({d.power_state})
              </span>
            )}
          </div>
        );
      },
    },
    {
      header: "Last connected",
      cell: (d) => (
        <span className="text-text-tertiary whitespace-nowrap">
          {d.last_connected !== null
            ? formatRelativeTime(d.last_connected)
            : "Never"}
        </span>
      ),
    },
    {
      header: "Actions",
      align: "right",
      cell: (d) => (
        <div
          className="flex items-center justify-end gap-1"
          onClick={(e) => e.stopPropagation()}
        >
          {d.assigned_user === null && canAssign(d.status) && (
            <button
              type="button"
              onClick={() => handleAssign(d)}
              aria-label={`Assign ${d.name}`}
              title="Assign to a user"
              className={iconBtn}
            >
              <UserPlus size={16} aria-hidden />
            </button>
          )}
          {d.assigned_user !== null && (
            <button
              type="button"
              onClick={() => handleUnassign(d)}
              aria-label={`Unassign ${d.name}`}
              title="Unassign"
              className={iconBtn}
            >
              <MinusCircle size={16} aria-hidden />
            </button>
          )}
          <PowerMenu
            desktop={d}
            isOpen={openMenuId === d.id}
            onToggle={() =>
              setOpenMenuId(openMenuId === d.id ? null : d.id)
            }
            onClose={() => setOpenMenuId(null)}
            onAction={(action) => handlePower(d, action)}
          />
          {canRebuild(d.status) && (
            <button
              type="button"
              onClick={() => handleRebuild(d)}
              aria-label={`Rebuild ${d.name}`}
              title="Rebuild"
              className={iconBtn}
            >
              <RotateCcw size={16} aria-hidden />
            </button>
          )}
          {canDestroy(d.status) && (
            <button
              type="button"
              onClick={() => handleDestroy(d)}
              aria-label={`Destroy ${d.name}`}
              title="Destroy"
              className={iconBtnDanger}
            >
              <Trash2 size={16} aria-hidden />
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
          Desktops
        </h1>
        <p className="text-body text-text-secondary mt-2">
          All cloned VMs across all pools. Assignment, lifecycle, and
          power actions live here. New desktops appear automatically as
          the pool provisioner creates them — there's no manual "Add
          desktop" path.
        </p>
      </header>

      <div className="max-w-6xl mx-auto">
        <div className="mb-4 flex flex-wrap gap-3 items-end">
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
                setStatusFilter(e.target.value as DesktopStatus | "")
              }
              className={filterInput}
            >
              <option value="">All statuses</option>
              {DESKTOP_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {desktopStatusBadge(s).label}
                </option>
              ))}
            </select>
          </FilterField>
          <FilterField label="Assigned user" htmlFor="filter-user">
            <input
              id="filter-user"
              type="text"
              placeholder="AD username"
              value={userFilter}
              onChange={(e) => setUserFilter(e.target.value)}
              className={filterInput}
            />
          </FilterField>
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
          data={desktops.data}
          isPending={desktops.isPending}
          error={desktops.error}
          onRetry={() => desktops.refetch()}
          rowKey={(d) => d.id}
          onRowClick={(d) => setSelectedId(d.id)}
          emptyMessage="No desktops match the current filters."
        />

        {desktops.data?.length === 50 && (
          <p className="mt-3 text-caption text-text-tertiary">
            Showing 50 desktops. Pagination is M5+; narrow filters to find
            a specific desktop.
          </p>
        )}
      </div>

      <DesktopDetailDrawer
        desktopId={selectedId}
        onClose={() => setSelectedId(null)}
      />
    </div>
  );
}


// ── Power menu (custom, no headless library) ─────────────────


interface PowerMenuProps {
  desktop: DesktopRead;
  isOpen: boolean;
  onToggle: () => void;
  onClose: () => void;
  onAction: (action: PowerAction) => void;
}


function PowerMenu({
  desktop,
  isOpen,
  onToggle,
  onClose,
  onAction,
}: PowerMenuProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    const handleOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("mousedown", handleOutside);
    window.addEventListener("keydown", handleEsc);
    return () => {
      window.removeEventListener("mousedown", handleOutside);
      window.removeEventListener("keydown", handleEsc);
    };
  }, [isOpen, onClose]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={onToggle}
        aria-label={`Power actions for ${desktop.name}`}
        aria-haspopup="menu"
        aria-expanded={isOpen}
        title="Power…"
        className={
          "inline-flex items-center justify-center gap-0.5 h-8 px-2 rounded-md " +
          "text-text-secondary hover:bg-surface-2 hover:text-text-primary " +
          "focus-visible:outline-none focus-visible:shadow-focus"
        }
      >
        <Power size={16} aria-hidden />
        <ChevronDown size={12} aria-hidden />
      </button>
      {isOpen && (
        <ul
          role="menu"
          className={
            "absolute right-0 mt-1 z-popover min-w-[8rem] py-1 " +
            "bg-surface-1 border border-border-default rounded-md shadow-md"
          }
        >
          <li>
            <MenuItem label="Start" onClick={() => onAction("start")} />
          </li>
          <li>
            <MenuItem
              label="Shutdown"
              onClick={() => onAction("shutdown")}
            />
          </li>
          <li>
            <MenuItem
              label="Reboot"
              onClick={() => onAction("reboot")}
            />
          </li>
          <li>
            <MenuItem
              label="Stop (hard)"
              onClick={() => onAction("stop")}
              danger
            />
          </li>
        </ul>
      )}
    </div>
  );
}


function MenuItem({
  label,
  onClick,
  danger = false,
}: {
  label: string;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className={
        "block w-full text-left px-3 py-1.5 text-body-sm " +
        "transition-colors duration-fast ease-out " +
        "focus-visible:outline-none focus-visible:bg-surface-2 " +
        (danger
          ? "text-danger-fg hover:bg-danger-bg"
          : "text-text-primary hover:bg-surface-2")
      }
    >
      {label}
    </button>
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


// ── Action visibility predicates (exported for tests) ──


export function canAssign(status: DesktopStatus): boolean {
  return status === "available" || status === "disconnected";
}

export function canRebuild(status: DesktopStatus): boolean {
  return (
    status !== "provisioning" &&
    status !== "deleting" &&
    status !== "connected"
  );
}

export function canDestroy(status: DesktopStatus): boolean {
  return status !== "provisioning" && status !== "deleting";
}

export function powerStateMatchesStatus(
  status: DesktopStatus,
  power_state: string,
): boolean {
  if (status === "connected" && power_state !== "running") return false;
  if (status === "deleting" && power_state === "running") return false;
  return true;
}


// ── Confirm-text builder ───────────────────────────────────


function powerConfirmText(name: string, action: PowerAction): string {
  switch (action) {
    case "start":
      return `Start "${name}"?`;
    case "shutdown":
      return (
        `Shut down "${name}" gracefully? The VM gets 120s to clean up; ` +
        "if it doesn't, the broker hard-stops it."
      );
    case "reboot":
      return `Reboot "${name}"? The VM is restarted from inside the guest OS.`;
    case "stop":
      return (
        `Hard-stop "${name}"? Equivalent to pulling the power cord. ` +
        "Use shutdown for graceful unless the VM is unresponsive."
      );
  }
}


function formatAssignError(exc: unknown, username: string): string {
  switch (brokerErrorCode(exc)) {
    case "CONFLICT":
      return (
        `User "${username}" already holds a desktop in this pool. ` +
        "Unassign that desktop first, then retry."
      );
    case "INVALID_REQUEST":
      return "Invalid username.";
    default:
      return "Assign failed. Check broker logs.";
  }
}

function formatUnassignError(exc: unknown): string {
  switch (brokerErrorCode(exc)) {
    case "CONFLICT":
      return "Desktop has an active session. End the session first, then retry.";
    default:
      return "Unassign failed. Check broker logs.";
  }
}

function formatPowerError(exc: unknown, action: PowerAction): string {
  switch (brokerErrorCode(exc)) {
    case "CONFLICT":
      return "Another task is in flight on this desktop. Wait, then refresh.";
    case "INVALID_REQUEST":
      return `"${action}" isn't a valid power action.`;
    case "SERVICE_UNAVAILABLE":
      return "Cluster is offline.";
    default:
      return `${action} failed. Check broker logs.`;
  }
}

function formatRebuildError(exc: unknown): string {
  switch (brokerErrorCode(exc)) {
    case "CONFLICT":
      return (
        "Either an active session or another task is in flight. " +
        "End the session and wait for the task to finish."
      );
    case "SERVICE_UNAVAILABLE":
      return "Cluster is offline.";
    default:
      return "Rebuild failed. Check broker logs.";
  }
}

function formatDestroyError(exc: unknown): string {
  switch (brokerErrorCode(exc)) {
    case "CONFLICT":
      return (
        "Either an active session or another task is in flight. " +
        "End the session and wait for the task to finish."
      );
    case "SERVICE_UNAVAILABLE":
      return "Cluster is offline.";
    default:
      return "Destroy failed. Check broker logs.";
  }
}


// ── Style constants (duplicated from sibling pages) ────────


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
const filterInput =
  "h-10 px-3 rounded-md border border-border-default bg-surface-1 " +
  "text-body-sm text-text-primary " +
  "focus-visible:outline-none focus-visible:shadow-focus";
const alertBanner =
  "mb-4 px-4 py-3 rounded-md " +
  "bg-danger-bg border border-danger-border text-danger-fg " +
  "text-body-sm";
const successBanner =
  "mb-4 px-4 py-3 rounded-md " +
  "bg-success-bg border border-success-border text-success-fg " +
  "text-body-sm";
