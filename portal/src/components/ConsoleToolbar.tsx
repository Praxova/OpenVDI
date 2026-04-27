import { Keyboard, LogOut, Loader2 } from "lucide-react";
import type { ReactNode } from "react";

export type ConsoleStateName =
  | "connecting"
  | "connected"
  | "disconnecting"
  | "disconnected"
  | "error";

interface ConsoleToolbarProps {
  state: ConsoleStateName;
  desktopName: string | null;
  errorReason: string | null;
  onCtrlAltDel: () => void;
  onDisconnect: () => void;
  /** Disable both buttons when the viewer is mid-mutation. */
  busy: boolean;
}

/**
 * Toolbar above the noVNC viewer. Height 48px (h-12 = --space-12),
 * surface-1 background, border-bottom, design-system tokens
 * throughout.
 *
 * Layout:
 *   [Indicator: status icon + label]                       [Ctrl+Alt+Del] [Disconnect]
 *
 * The toolbar is intentionally compact — the user's focus is the
 * canvas below. Buttons use design-system §8.1 ghost-icon-button +
 * secondary-button styling respectively.
 */
export function ConsoleToolbar({
  state,
  desktopName,
  errorReason,
  onCtrlAltDel,
  onDisconnect,
  busy,
}: ConsoleToolbarProps) {
  const ctrlAltDelDisabled = state !== "connected";
  const disconnectDisabled =
    busy || state === "disconnected" || state === "error";
  const disconnectLabel =
    state === "disconnecting" ? "Disconnecting…" : "Disconnect";

  return (
    <header
      role="toolbar"
      aria-label="Console controls"
      className={
        "flex-none h-12 flex items-center justify-between gap-4 px-6 " +
        "bg-surface-1 border-b border-border-subtle"
      }
    >
      <ConnectionIndicator
        state={state}
        desktopName={desktopName}
        errorReason={errorReason}
      />

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onCtrlAltDel}
          disabled={ctrlAltDelDisabled}
          aria-label="Send Ctrl+Alt+Del"
          title="Send Ctrl+Alt+Del"
          className={
            "inline-flex items-center justify-center h-8 px-3 rounded-md " +
            "text-text-primary text-body-sm font-medium gap-2 " +
            "transition-colors duration-fast ease-out " +
            "hover:bg-surface-2 " +
            "focus-visible:outline-none focus-visible:shadow-focus " +
            "disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-transparent"
          }
        >
          <Keyboard size={16} strokeWidth={2} aria-hidden />
          <span className="hidden sm:inline">Ctrl+Alt+Del</span>
        </button>

        <button
          type="button"
          onClick={onDisconnect}
          disabled={disconnectDisabled}
          aria-label={disconnectLabel}
          className={
            "inline-flex items-center gap-2 h-8 px-3 rounded-md " +
            "bg-action-secondary text-action-secondary-text text-body-sm font-medium " +
            "transition-colors duration-fast ease-out " +
            "hover:opacity-90 " +
            "focus-visible:outline-none focus-visible:shadow-focus " +
            "disabled:opacity-50 disabled:cursor-not-allowed"
          }
        >
          {state === "disconnecting" ? (
            <Loader2
              size={16}
              strokeWidth={2}
              className="animate-spin"
              aria-hidden
            />
          ) : (
            <LogOut size={16} strokeWidth={2} aria-hidden />
          )}
          <span>{disconnectLabel}</span>
        </button>
      </div>
    </header>
  );
}

interface ConnectionIndicatorProps {
  state: ConsoleStateName;
  desktopName: string | null;
  errorReason: string | null;
}

/**
 * Status pill on the left of the toolbar. Combines a small color
 * indicator (dot or spinner) with a label. Designed to be glanceable —
 * green dot at left = "everything is fine", anything else demands
 * attention.
 */
function ConnectionIndicator({
  state,
  desktopName,
  errorReason,
}: ConnectionIndicatorProps) {
  const display: { dot: ReactNode; label: string } = (() => {
    switch (state) {
      case "connecting":
        return {
          dot: (
            <Loader2
              size={16}
              strokeWidth={2}
              className="animate-spin text-info-fg"
              aria-hidden
            />
          ),
          label:
            desktopName !== null
              ? `Connecting to ${desktopName}…`
              : "Connecting…",
        };
      case "connected":
        return {
          dot: <Dot tone="success" />,
          label:
            desktopName !== null ? `Connected to ${desktopName}` : "Connected",
        };
      case "disconnecting":
        return {
          dot: (
            <Loader2
              size={16}
              strokeWidth={2}
              className="animate-spin text-text-tertiary"
              aria-hidden
            />
          ),
          label: "Disconnecting…",
        };
      case "disconnected":
        return {
          dot: <Dot tone="neutral" />,
          label: "Disconnected",
        };
      case "error":
        return {
          dot: <Dot tone="danger" />,
          label: errorReason ?? "Connection error",
        };
    }
  })();

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center gap-2 min-w-0"
    >
      <span className="flex-none flex items-center justify-center w-4 h-4">
        {display.dot}
      </span>
      <span className="text-body-sm text-text-primary truncate">
        {display.label}
      </span>
    </div>
  );
}

function Dot({ tone }: { tone: "success" | "neutral" | "danger" }) {
  const cls: Record<typeof tone, string> = {
    success: "bg-success-solid",
    neutral: "bg-text-tertiary",
    danger: "bg-danger-solid",
  };
  return (
    <span className={`block w-2 h-2 rounded-full ${cls[tone]}`} aria-hidden />
  );
}
