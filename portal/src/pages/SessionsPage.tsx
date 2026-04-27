import { useState, useCallback } from "react";
import type { ReactNode } from "react";
import { ServerOff, AlertTriangle, RefreshCw } from "lucide-react";

import {
  useSessionsQuery,
  useDisconnectSessionMutation,
} from "@/api/sessions";
import { brokerErrorCode } from "@/api/errors";
import { SessionRow } from "@/components/SessionRow";
import type { UserSessionView } from "@/types";

export function SessionsPage() {
  const [includeEnded, setIncludeEnded] = useState(false);
  const [disconnectingId, setDisconnectingId] = useState<string | null>(null);

  const { data, error, isPending, refetch, isRefetching } =
    useSessionsQuery(includeEnded);

  const disconnectMutation = useDisconnectSessionMutation();

  const handleDisconnect = useCallback(
    (sessionId: string) => {
      // Serialize: ignore additional clicks while one is in flight.
      // The hook would queue them anyway, but the per-row UI tracks
      // a single in-flight id, so only one Disconnecting… spinner
      // shows at a time.
      if (disconnectingId !== null) return;
      setDisconnectingId(sessionId);
      disconnectMutation.mutate(sessionId, {
        onSettled: () => setDisconnectingId(null),
      });
    },
    [disconnectingId, disconnectMutation],
  );

  return (
    <div className="px-6 py-8">
      <header className="max-w-6xl mx-auto mb-6 flex items-end justify-between gap-4 flex-wrap">
        <div>
          <h1 className="font-display text-h1 font-semibold text-text-primary">
            Your sessions
          </h1>
          <p className="text-body text-text-secondary mt-2">
            Connections you've made to desktops. Disconnect any active session
            here, or use the launcher to start a new one.
          </p>
        </div>
        <FilterControl value={includeEnded} onChange={setIncludeEnded} />
      </header>

      <div className="max-w-6xl mx-auto">
        {isPending ? (
          <LoadingState />
        ) : error !== null ? (
          <ErrorState
            error={error}
            onRetry={() => refetch()}
            isRetrying={isRefetching}
          />
        ) : data.length === 0 ? (
          <EmptyState includeEnded={includeEnded} />
        ) : (
          <SessionsTable
            sessions={data}
            onDisconnect={handleDisconnect}
            disconnectingId={disconnectingId}
          />
        )}
      </div>
    </div>
  );
}

// ── Filter ─────────────────────────────────────────────────────

interface FilterControlProps {
  value: boolean;
  onChange: (next: boolean) => void;
}

/**
 * Two-button segmented control. value=false → "Active" highlighted.
 * value=true → "All" highlighted.
 *
 * Uses aria-pressed (toggle button semantics) inside an
 * aria-label'd group. Per WAI-ARIA Authoring Practices, segmented
 * controls of two-or-more radio-like options can use either
 * `role="radiogroup"` + `role="radio"` OR a group of toggle
 * buttons. Toggle buttons are simpler when each "option" maps
 * directly to a value the user can flip.
 */
function FilterControl({ value, onChange }: FilterControlProps) {
  return (
    <div
      role="group"
      aria-label="Session filter"
      className={
        "inline-flex items-center bg-surface-1 border border-border-subtle " +
        "rounded-md p-1 gap-1"
      }
    >
      <FilterButton selected={!value} onClick={() => onChange(false)}>
        Active
      </FilterButton>
      <FilterButton selected={value} onClick={() => onChange(true)}>
        All
      </FilterButton>
    </div>
  );
}

interface FilterButtonProps {
  selected: boolean;
  onClick: () => void;
  children: ReactNode;
}

function FilterButton({ selected, onClick, children }: FilterButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={
        "px-3 h-8 rounded-sm text-body-sm font-medium " +
        "transition-colors duration-fast ease-out " +
        "focus-visible:outline-none focus-visible:shadow-focus " +
        (selected
          ? "bg-action-primary text-text-on-accent"
          : "text-text-secondary hover:bg-surface-2")
      }
    >
      {children}
    </button>
  );
}

// ── Sessions table ─────────────────────────────────────────────

interface SessionsTableProps {
  sessions: UserSessionView[];
  onDisconnect: (sessionId: string) => void;
  disconnectingId: string | null;
}

function SessionsTable({
  sessions,
  onDisconnect,
  disconnectingId,
}: SessionsTableProps) {
  return (
    <div
      className={
        "bg-surface-1 border border-border-subtle rounded-lg " +
        "overflow-hidden"
      }
    >
      <div className="overflow-x-auto">
        <table className="w-full text-left">
          <thead className="bg-surface-2">
            <tr className="text-text-tertiary text-caption uppercase tracking-wide">
              <th scope="col" className="px-4 py-3 font-medium">
                Desktop
              </th>
              <th scope="col" className="px-4 py-3 font-medium">
                Pool
              </th>
              <th scope="col" className="px-4 py-3 font-medium">
                Status
              </th>
              <th scope="col" className="px-4 py-3 font-medium">
                Connected
              </th>
              <th scope="col" className="px-4 py-3 font-medium text-right">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((s) => (
              <SessionRow
                key={s.id}
                session={s}
                onDisconnect={onDisconnect}
                isDisconnecting={disconnectingId === s.id}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── States: loading / error / empty ────────────────────────────

function LoadingState() {
  return (
    <div
      className="bg-surface-1 border border-border-subtle rounded-lg p-6"
      role="status"
      aria-label="Loading your sessions"
    >
      <div className="space-y-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex items-center gap-4">
            <div className="h-4 w-32 rounded-sm bg-surface-2 animate-pulse" />
            <div className="h-4 w-40 rounded-sm bg-surface-2 animate-pulse" />
            <div className="h-5 w-16 rounded-sm bg-surface-2 animate-pulse" />
            <div className="h-4 w-24 rounded-sm bg-surface-2 animate-pulse" />
            <div className="ml-auto h-8 w-24 rounded-md bg-surface-2 animate-pulse" />
          </div>
        ))}
      </div>
    </div>
  );
}

interface EmptyStateProps {
  includeEnded: boolean;
}

function EmptyState({ includeEnded }: EmptyStateProps) {
  // Two distinct empty cases. "No active sessions" with the All filter
  // off is the common case — user has just disconnected from
  // everything. "No sessions at all" with the All filter on is the
  // first-time user case.
  const title = includeEnded ? "No sessions yet" : "No active sessions";
  const body = includeEnded
    ? "You haven't connected to a desktop yet. Head to the launcher to start a session."
    : "You're not currently connected to any desktops. Use the launcher to start a session, or toggle to All to see your history.";

  return (
    <div
      className={
        "min-h-80 flex flex-col items-center justify-center text-center " +
        "px-6 bg-surface-1 border border-border-subtle rounded-lg"
      }
    >
      <ServerOff
        size={32}
        strokeWidth={1.5}
        className="text-text-tertiary"
        aria-hidden
      />
      <h2 className="font-body text-h3 font-semibold text-text-primary mt-4">
        {title}
      </h2>
      <p className="text-body text-text-secondary mt-2 max-w-md">{body}</p>
    </div>
  );
}

interface ErrorStateProps {
  error: Error;
  onRetry: () => void;
  isRetrying: boolean;
}

function ErrorState({ error, onRetry, isRetrying }: ErrorStateProps) {
  const message = errorMessageFor(error);

  return (
    <div
      role="alert"
      className={
        "min-h-80 flex flex-col items-center justify-center text-center " +
        "px-6 bg-surface-1 border border-border-subtle rounded-lg"
      }
    >
      <AlertTriangle
        size={32}
        strokeWidth={1.5}
        className="text-danger-fg"
        aria-hidden
      />
      <h2 className="font-body text-h3 font-semibold text-text-primary mt-4">
        Couldn't load your sessions
      </h2>
      <p className="text-body text-text-secondary mt-2 max-w-md">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        disabled={isRetrying}
        className={
          "mt-6 inline-flex items-center gap-2 h-10 px-4 rounded-md " +
          "bg-action-secondary text-action-secondary-text text-body font-medium " +
          "transition-colors duration-fast ease-out " +
          "hover:opacity-90 " +
          "focus-visible:outline-none focus-visible:shadow-focus " +
          "disabled:opacity-50 disabled:cursor-not-allowed"
        }
      >
        <RefreshCw
          size={16}
          strokeWidth={2}
          className={isRetrying ? "animate-spin" : ""}
          aria-hidden
        />
        {isRetrying ? "Retrying…" : "Try again"}
      </button>
    </div>
  );
}

/**
 * Map an error to a user-facing message for the sessions list.
 *
 * Mirrors the M3-04 launcher's error mapping but with
 * sessions-specific phrasing. Both pages now use brokerErrorCode()
 * to extract the code; lifting the dispatch itself into a generic
 * helper would tangle context-specific copy across pages.
 */
function errorMessageFor(error: Error): string {
  switch (brokerErrorCode(error)) {
    case "UNAUTHORIZED":
      return "Your session has expired. Sign out and back in to continue.";
    case "FORBIDDEN":
      return "You don't have permission to view your sessions. Contact an administrator.";
    case "INTERNAL_ERROR":
    case "ERROR":
      return "Something went wrong loading your sessions. Try again, or contact an administrator if it persists.";
    default:
      return "We couldn't load your sessions. Try again, or contact an administrator if it persists.";
  }
}
