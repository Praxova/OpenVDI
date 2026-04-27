import { ServerOff, AlertTriangle, RefreshCw } from "lucide-react";

import { useDesktopsQuery } from "@/api/desktops";
import { PoolCard } from "@/components/PoolCard";
import type { UserPoolView } from "@/types";

export function DesktopsPage() {
  const { data, error, isPending, refetch, isRefetching } = useDesktopsQuery();

  return (
    <div className="px-6 py-8">
      <header className="max-w-6xl mx-auto mb-6">
        <h1 className="font-display text-h1 font-semibold text-text-primary">
          Your desktops
        </h1>
        <p className="text-body text-text-secondary mt-2">
          Pools you have access to. Click Connect to open a desktop in your browser.
        </p>
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
          <EmptyState />
        ) : (
          <PoolGrid pools={data} />
        )}
      </div>
    </div>
  );
}

// ── Sub-states ───────────────────────────────────────────────

function PoolGrid({ pools }: { pools: UserPoolView[] }) {
  return (
    <div
      className={
        "grid gap-6 " +
        "grid-cols-1 md:grid-cols-2 lg:grid-cols-3"
      }
    >
      {pools.map((pool) => (
        <PoolCard key={pool.id} pool={pool} />
      ))}
    </div>
  );
}

function LoadingState() {
  // Three skeleton cards. Per design-system.md §8.12.1: shape the
  // skeleton like the eventual content. Reduced-motion is handled at
  // the user-agent level — Tailwind's `animate-pulse` becomes a no-op
  // under `prefers-reduced-motion: reduce`.
  return (
    <div
      className="grid gap-6 grid-cols-1 md:grid-cols-2 lg:grid-cols-3"
      role="status"
      aria-label="Loading your desktops"
    >
      {[0, 1, 2].map((i) => (
        <SkeletonCard key={i} />
      ))}
    </div>
  );
}

function SkeletonCard() {
  return (
    <div
      className={
        "bg-surface-1 border border-border-subtle rounded-lg shadow-sm " +
        "p-6 flex flex-col gap-4"
      }
      aria-hidden
    >
      <div className="h-5 w-2/3 rounded-sm bg-surface-2 animate-pulse" />
      <div className="h-4 w-full rounded-sm bg-surface-2 animate-pulse" />
      <div className="h-4 w-1/2 rounded-sm bg-surface-2 animate-pulse" />
      <div className="mt-auto pt-4 flex justify-end">
        <div className="h-10 w-24 rounded-md bg-surface-2 animate-pulse" />
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div
      className={
        "min-h-80 flex flex-col items-center justify-center text-center " +
        "px-6"
      }
    >
      <ServerOff
        size={32}
        strokeWidth={1.5}
        className="text-text-tertiary"
        aria-hidden
      />
      <h2 className="font-body text-h3 font-semibold text-text-primary mt-4">
        No desktops yet
      </h2>
      <p className="text-body text-text-secondary mt-2 max-w-md">
        You aren't entitled to any desktop pools. Ask an administrator to add
        you to a pool.
      </p>
    </div>
  );
}

interface ErrorStateProps {
  error: Error;
  onRetry: () => void;
  isRetrying: boolean;
}

function ErrorState({ error, onRetry, isRetrying }: ErrorStateProps) {
  // Dispatch on `error.code` per FE4. The code is set by M2-11's
  // exception handler family and is stable across HTTP statuses for
  // the same logical condition.
  const message = errorMessageFor(error);

  return (
    <div
      role="alert"
      className={
        "min-h-80 flex flex-col items-center justify-center text-center " +
        "px-6"
      }
    >
      <AlertTriangle
        size={32}
        strokeWidth={1.5}
        className="text-danger-fg"
        aria-hidden
      />
      <h2 className="font-body text-h3 font-semibold text-text-primary mt-4">
        Couldn't load your desktops
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
        {isRetrying ? "Retrying..." : "Try again"}
      </button>
    </div>
  );
}

/**
 * Map an error to a user-facing message.
 *
 * Authentication failures shouldn't reach this page — `<ProtectedRoute>`
 * bounces to /login first — but defensively handle 401/UNAUTHORIZED
 * in case a session expires mid-fetch (theoretically possible in M4
 * once JWTs have a TTL).
 *
 * Per design-system.md §9 *Copy voice*: state the situation, offer
 * the next step. Never expose internal error details to the user.
 */
function errorMessageFor(error: Error): string {
  // The TanStack Register declaration in lib/queryClient.ts pins the
  // error type to BrokerError. The duck-type check here is defensive
  // against a future change that swaps the error type.
  if (error && typeof error === "object" && "code" in error) {
    const code = (error as { code: string }).code;
    if (code === "UNAUTHORIZED") {
      return "Your session has expired. Sign out and back in to continue.";
    }
    if (code === "FORBIDDEN") {
      return "You don't have permission to view your desktop list. Contact an administrator.";
    }
    if (code === "INTERNAL_ERROR" || code === "ERROR") {
      return "Something went wrong loading your desktops. Try again, or contact an administrator if it persists.";
    }
  }
  return "We couldn't load your desktops. Try again, or contact an administrator if it persists.";
}
