import { Link } from "react-router-dom";
import { Monitor } from "lucide-react";

import {
  StatusBadge,
  desktopStatusBadge,
  poolStatusBadge,
  poolTypeBadge,
} from "./StatusBadge";
import { formatRelativeTime } from "@/lib/time";
import type { UserPoolView } from "@/types";

interface PoolCardProps {
  pool: UserPoolView;
}

/**
 * Card for one entitled pool.
 *
 * Rendering naming convention (pinned in M3 seed):
 *   - Header title             → pool.display_name (NEVER pool.name slug)
 *   - Assigned-desktop label   → pool.assigned_desktop.name (the VM name "ENG-003" — IS user-facing)
 *   - URL fragment             → pool.id (UUID)
 *
 * Connect-button posture: always enabled, label varies on whether
 * an assignment exists. The broker is the source of truth for
 * whether a connect actually succeeds. Pool state at launcher-paint
 * time can be stale within seconds; gating the button on state
 * here would only reflect that staleness. M3-06 surfaces 503/409
 * from the broker as inline errors at the connect call site.
 */
export function PoolCard({ pool }: PoolCardProps) {
  const statusBadge = poolStatusBadge(pool.status);
  const typeBadge = poolTypeBadge(pool.pool_type);
  const hasAssignment = pool.assigned_desktop !== null;
  const buttonLabel = hasAssignment ? "Resume" : "Connect";
  const consoleHref = `/desktops/${pool.id}/console`;

  return (
    <article
      className={
        "flex flex-col " +
        "bg-surface-1 border border-border-subtle rounded-lg shadow-sm " +
        "transition-shadow duration-fast ease-out hover:shadow-md"
      }
      aria-labelledby={`pool-${pool.id}-title`}
    >
      <header
        className={
          "flex items-start justify-between gap-3 " +
          "px-6 py-4 border-b border-border-subtle"
        }
      >
        <h2
          id={`pool-${pool.id}-title`}
          className="font-body text-h3 font-semibold text-text-primary"
        >
          {pool.display_name}
        </h2>
        <StatusBadge tone={statusBadge.tone} label={statusBadge.label} />
      </header>

      <div className="flex-1 px-6 py-4 flex flex-col gap-4">
        {pool.description !== null && pool.description !== "" && (
          <p className="text-body-sm text-text-secondary">{pool.description}</p>
        )}

        <div className="flex items-center gap-2">
          <StatusBadge tone={typeBadge.tone} label={typeBadge.label} />
        </div>

        {hasAssignment && pool.assigned_desktop !== null && (
          <AssignedDesktopSummary desktop={pool.assigned_desktop} />
        )}
      </div>

      <footer className="px-6 py-4 border-t border-border-subtle flex justify-end">
        <Link
          to={consoleHref}
          aria-label={`${buttonLabel} ${pool.display_name}`}
          className={
            "inline-flex items-center gap-2 h-10 px-4 rounded-md " +
            "bg-action-primary text-text-on-accent text-body font-medium " +
            "transition-colors duration-fast ease-out " +
            "hover:bg-action-primary-hover " +
            "active:bg-action-primary-active " +
            "focus-visible:outline-none focus-visible:shadow-focus"
          }
        >
          <Monitor size={16} strokeWidth={2} aria-hidden />
          {buttonLabel}
        </Link>
      </footer>
    </article>
  );
}

interface AssignedDesktopSummaryProps {
  desktop: NonNullable<UserPoolView["assigned_desktop"]>;
}

/**
 * Inline summary of the user's current assignment in this pool.
 * Renders the desktop's user-facing name (e.g. "ENG-003") plus its
 * status badge. The desktop's `last_connected` is rendered as a
 * relative-time when present, formatted by the browser's locale.
 */
function AssignedDesktopSummary({ desktop }: AssignedDesktopSummaryProps) {
  const badge = desktopStatusBadge(desktop.status);
  return (
    <section
      aria-label="Current assignment"
      className={
        "rounded-md p-3 " +
        "bg-surface-2 border border-border-subtle"
      }
    >
      <div className="flex items-center justify-between gap-3">
        <span className="font-mono text-code text-text-primary">
          {desktop.name}
        </span>
        <StatusBadge tone={badge.tone} label={badge.label} />
      </div>
      {desktop.last_connected !== null && (
        <p className="text-caption text-text-tertiary mt-1">
          Last connected {formatRelativeTime(desktop.last_connected)}
        </p>
      )}
    </section>
  );
}
