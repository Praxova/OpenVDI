import { LogOut, Loader2 } from "lucide-react";

import { StatusBadge, sessionStatusBadge } from "./StatusBadge";
import { formatRelativeTime } from "@/lib/time";
import type { UserSessionView } from "@/types";

interface SessionRowProps {
  session: UserSessionView;
  onDisconnect: (sessionId: string) => void;
  /**
   * True when THIS specific row's session is currently being
   * disconnected. The page tracks `disconnectingId` and threads it
   * through; multiple rows are never simultaneously disconnecting
   * because the page serializes (a click while one is in flight
   * is ignored).
   */
  isDisconnecting: boolean;
}

/**
 * One row in the sessions table.
 *
 * Orphan handling: when the broker has deleted the desktop backing a
 * session (admin recycle, pool destroy, etc.), the broker zeroes out
 * `desktop_id` / `desktop_name` / `pool_id` / `pool_name` but keeps
 * the session row in the database for troubleshooting/audit. We
 * render those as italic placeholders so the user understands what
 * they're looking at — vs. blanks, which read like a rendering bug.
 *
 * `connected_at` is nullable on the wire (a session may be in
 * `connecting` state with no timestamp yet); we render `—` until
 * the broker writes the connection time.
 */
export function SessionRow({
  session,
  onDisconnect,
  isDisconnecting,
}: SessionRowProps) {
  const badge = sessionStatusBadge(session.status);
  const isActive = session.status === "active";

  return (
    <tr className="border-t border-border-subtle">
      <td className="px-4 py-3">
        {session.desktop_name !== null ? (
          <span className="font-mono text-code text-text-primary">
            {session.desktop_name}
          </span>
        ) : (
          <span className="text-text-tertiary italic">(desktop deleted)</span>
        )}
      </td>
      <td className="px-4 py-3 text-text-secondary text-body-sm">
        {session.pool_name !== null ? (
          session.pool_name
        ) : (
          <span className="text-text-tertiary">—</span>
        )}
      </td>
      <td className="px-4 py-3">
        <StatusBadge tone={badge.tone} label={badge.label} />
      </td>
      <td className="px-4 py-3 text-text-secondary text-body-sm whitespace-nowrap">
        {session.connected_at !== null ? (
          formatRelativeTime(session.connected_at)
        ) : (
          <span className="text-text-tertiary">—</span>
        )}
      </td>
      <td className="px-4 py-3 text-right">
        {isActive ? (
          <button
            type="button"
            onClick={() => onDisconnect(session.id)}
            disabled={isDisconnecting}
            aria-label={
              session.desktop_name !== null
                ? `Disconnect ${session.desktop_name}`
                : "Disconnect this session"
            }
            className={
              "inline-flex items-center gap-2 h-8 px-3 rounded-md " +
              "bg-action-secondary text-action-secondary-text text-body-sm font-medium " +
              "transition-colors duration-fast ease-out " +
              "hover:opacity-90 " +
              "focus-visible:outline-none focus-visible:shadow-focus " +
              "disabled:opacity-50 disabled:cursor-not-allowed"
            }
          >
            {isDisconnecting ? (
              <Loader2
                size={14}
                strokeWidth={2}
                className="animate-spin"
                aria-hidden
              />
            ) : (
              <LogOut size={14} strokeWidth={2} aria-hidden />
            )}
            <span>{isDisconnecting ? "Disconnecting…" : "Disconnect"}</span>
          </button>
        ) : null}
      </td>
    </tr>
  );
}
