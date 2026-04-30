import type { DesktopStatus, PoolStatus, SessionStatus } from "@/types";
import type { ClusterStatus } from "@/types/admin";

export type StatusTone = "info" | "success" | "warning" | "danger" | "neutral";

interface StatusBadgeProps {
  tone: StatusTone;
  label: string;
  className?: string;
}

/**
 * Status pill. Rendered inline in cards and rows; never decorative.
 *
 * Per design-system.md §2.4 status colors: "Status colors are never
 * used as decoration. Reserve them for actual status communication."
 * The `neutral` tone exists for purely informational labels (pool
 * type, etc.) that aren't a status assertion — it uses tertiary text
 * on surface-2, no color signal.
 */
export function StatusBadge({ tone, label, className = "" }: StatusBadgeProps) {
  const toneClasses: Record<StatusTone, string> = {
    info:    "bg-info-bg text-info-fg border-info-border",
    success: "bg-success-bg text-success-fg border-success-border",
    warning: "bg-warning-bg text-warning-fg border-warning-border",
    danger:  "bg-danger-bg text-danger-fg border-danger-border",
    neutral: "bg-surface-2 text-text-tertiary border-border-subtle",
  };

  return (
    <span
      className={
        "inline-flex items-center px-2 py-0.5 rounded-sm border " +
        "text-caption font-medium font-tabular " +
        toneClasses[tone] + " " + className
      }
    >
      {label}
    </span>
  );
}

// ── Status → badge translators ───────────────────────────────

interface BadgeShape {
  tone: StatusTone;
  label: string;
}

/**
 * Translate a `PoolStatus` to a badge.
 *
 * Mappings:
 *   active        → success    "Active"
 *   provisioning  → info       "Provisioning"
 *   draining      → warning    "Draining"
 *   disabled      → warning    "Disabled"
 *   error         → danger     "Error"
 *   deleting      → danger     "Deleting"
 *
 * Exhaustive switch — adding a new PoolStatus variant without
 * updating here is a TypeScript error (per ST15 strict + the type's
 * literal union). No default branch by design.
 */
export function poolStatusBadge(status: PoolStatus): BadgeShape {
  switch (status) {
    case "active":       return { tone: "success", label: "Active" };
    case "provisioning": return { tone: "info",    label: "Provisioning" };
    case "draining":     return { tone: "warning", label: "Draining" };
    case "disabled":     return { tone: "warning", label: "Disabled" };
    case "error":        return { tone: "danger",  label: "Error" };
    case "deleting":     return { tone: "danger",  label: "Deleting" };
  }
}

/**
 * Translate a `DesktopStatus` to a badge.
 *
 *   provisioning  → info       "Provisioning"
 *   available     → success    "Ready"          — friendlier than "Available"
 *   assigned      → success    "Assigned"
 *   connected     → success    "In use"         — clearer than "Connected"
 *   disconnected  → warning    "Disconnected"
 *   error         → danger     "Error"
 *   deleting      → danger     "Deleting"
 *   maintenance   → warning    "Maintenance"
 */
export function desktopStatusBadge(status: DesktopStatus): BadgeShape {
  switch (status) {
    case "provisioning": return { tone: "info",    label: "Provisioning" };
    case "available":    return { tone: "success", label: "Ready" };
    case "assigned":     return { tone: "success", label: "Assigned" };
    case "connected":    return { tone: "success", label: "In use" };
    case "disconnected": return { tone: "warning", label: "Disconnected" };
    case "error":        return { tone: "danger",  label: "Error" };
    case "deleting":     return { tone: "danger",  label: "Deleting" };
    case "maintenance":  return { tone: "warning", label: "Maintenance" };
  }
}

/**
 * Translate a `SessionStatus` to a badge.
 *
 * The actual broker enum (`broker/app/models/session.py`) is
 * `connecting | active | disconnected | ended`. The M3-07 prompt
 * referenced `errored | timed_out` variants which the broker hasn't
 * shipped — when those land, extend this switch alongside that
 * milestone.
 *
 *   connecting    → info       "Connecting"   — brokering in flight
 *   active        → success    "Active"       — currently connected
 *   disconnected  → neutral    "Disconnected" — clean termination, no signal needed
 *   ended         → neutral    "Ended"        — terminal state, no signal needed
 *
 * `disconnected` and `ended` are both `neutral` rather than `warning`
 * because both are EXPECTED end states — flagging them as warning-toned
 * over-emphasizes a non-event.
 */
export function sessionStatusBadge(status: SessionStatus): BadgeShape {
  switch (status) {
    case "connecting":   return { tone: "info",    label: "Connecting" };
    case "active":       return { tone: "success", label: "Active" };
    case "disconnected": return { tone: "neutral", label: "Disconnected" };
    case "ended":        return { tone: "neutral", label: "Ended" };
  }
}

/**
 * Translate a `pool_type` to a neutral badge. Used in PoolCard to
 * communicate "this is a persistent vs nonpersistent pool" without
 * implying a status signal — neutral tone, not success/warning.
 */
export function poolTypeBadge(
  poolType: "persistent" | "nonpersistent",
): BadgeShape {
  return poolType === "persistent"
    ? { tone: "neutral", label: "Persistent" }
    : { tone: "neutral", label: "Non-persistent" };
}

/**
 * Translate a `ClusterStatus` to a badge.
 *
 *   pending      → neutral    "Pending"
 *   active       → success    "Active"
 *   maintenance  → warning    "Maintenance"
 *   offline      → danger     "Offline"
 */
export function clusterStatusBadge(status: ClusterStatus): BadgeShape {
  switch (status) {
    case "pending":     return { tone: "neutral", label: "Pending" };
    case "active":      return { tone: "success", label: "Active" };
    case "maintenance": return { tone: "warning", label: "Maintenance" };
    case "offline":     return { tone: "danger",  label: "Offline" };
  }
}
