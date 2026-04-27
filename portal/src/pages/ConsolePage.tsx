import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, RefreshCw } from "lucide-react";

import {
  ConsoleToolbar,
  type ConsoleStateName,
} from "@/components/ConsoleToolbar";
import {
  NoVNCViewer,
  type NoVNCViewerHandle,
} from "@/components/NoVNCViewer";
import { useAuth } from "@/auth/AuthContext";
import { useConnectMutation } from "@/api/connect";
import { useDisconnectSessionMutation } from "@/api/sessions";
import { BrokerError } from "@/api/errors";
import type { NoVNCTicketRead, ConnectResponse } from "@/types";

/**
 * The /desktops/:poolId/console page.
 *
 * Lifecycle:
 *   1. Mount with :poolId from URL.
 *   2. One-shot fire of useConnectMutation (StrictMode-safe via ref).
 *   3. On mutation success: store session_id + ticket, render viewer.
 *   4. Viewer's onConnect → state="connected". onDisconnect →
 *      state="disconnected". onError → state="error".
 *   5. User clicks Disconnect → fire useDisconnectSessionMutation,
 *      then navigate back to /desktops.
 *   6. User navigates away or closes tab → best-effort keepalive
 *      DELETE so the broker session doesn't dangle.
 */
export function ConsolePage() {
  const { poolId } = useParams<{ poolId: string }>();
  const navigate = useNavigate();
  const { currentUser } = useAuth();

  const connectMutation = useConnectMutation();
  const disconnectMutation = useDisconnectSessionMutation();

  // The connect-result ticket + session_id, once available.
  const [connectResult, setConnectResult] = useState<ConnectResponse | null>(
    null,
  );

  // The viewer's reported lifecycle state. Start at "connecting" —
  // the page is "trying to connect" from t=0, including before the
  // mutation has had a chance to fire.
  const [viewerState, setViewerState] =
    useState<ConsoleStateName>("connecting");

  // The reason string for the error state.
  const [errorReason, setErrorReason] = useState<string | null>(null);

  // ── Refs for cleanup paths ────────────────────────────────

  const didMountRef = useRef(false);
  const sessionIdRef = useRef<string | null>(null);
  const disconnectFiredRef = useRef(false);
  const viewerHandleRef = useRef<NoVNCViewerHandle | null>(null);

  // Keep sessionIdRef in sync — the cleanup paths read it without
  // closing over the React state directly.
  useEffect(() => {
    sessionIdRef.current = connectResult?.session_id ?? null;
  }, [connectResult]);

  // ── One-shot connect on mount (StrictMode-safe) ───────────

  // Why a ref-guarded one-shot rather than a fire-on-mount effect
  // with [poolId] deps: StrictMode in dev fires effects twice. The
  // mutation has no built-in dedupe; without this guard, dev would
  // make two POST /me/desktops/{id}/connect calls. The broker's
  // per-user-per-pool advisory lock (M2-08 decision S-C4) serializes
  // them — both end up with the same desktop — but the second call
  // burns a fresh VNC ticket from Proxmox unnecessarily. The guard
  // keeps the page exactly-once even in dev.
  useEffect(() => {
    if (didMountRef.current) return;
    if (poolId === undefined) return;
    didMountRef.current = true;
    connectMutation.mutate(poolId, {
      onSuccess: (result) => {
        setConnectResult(result);
        // viewerState stays "connecting" — the viewer fires onConnect
        // when its WebSocket completes the VNC handshake.
      },
      onError: (error) => {
        setViewerState("error");
        setErrorReason(connectErrorMessage(error));
      },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [poolId]);

  // ── Cleanup: SPA-nav-away + tab-close ──────────────────────

  // We fire DELETE /me/sessions/{id} as best-effort cleanup in two
  // paths: (a) the page unmounts via SPA navigation, (b) the user
  // closes the tab. Both use fetch with `keepalive: true` so the
  // request survives the page tear-down.
  //
  // Why not the disconnectMutation here? The mutation's
  // invalidations + state updates require a live React tree; on
  // SPA-nav, that tree is being torn down, and on tab-close it's
  // gone. fetch(keepalive) is the right tool — fire and forget.
  //
  // The broker's DELETE is idempotent on already-ended sessions,
  // so a duplicate (e.g. the user clicked Disconnect, the mutation
  // succeeded, then they navigated away) costs one extra 204.
  useEffect(() => {
    const fireKeepalive = () => {
      const sessionId = sessionIdRef.current;
      if (sessionId === null || disconnectFiredRef.current) return;
      if (currentUser === null) return;
      disconnectFiredRef.current = true;
      // Headers mirror BrokerClientProvider's getAuthHeaders. Inline
      // here because the keepalive path doesn't go through BrokerClient
      // (we want the literal `fetch(..., { keepalive: true })` API).
      // M4 LDAP swap will need to update this site AND the
      // BrokerClientProvider in the same diff — search for "X-Dev-User"
      // to find both.
      try {
        void fetch(`/api/v1/me/sessions/${sessionId}`, {
          method: "DELETE",
          headers: {
            "X-Dev-User": currentUser.username,
            "X-Dev-Groups": currentUser.groups.join(","),
            "X-Dev-Role": currentUser.role,
          },
          keepalive: true,
        });
      } catch {
        // Best effort — ignore.
      }
    };

    const handleBeforeUnload = () => fireKeepalive();
    window.addEventListener("beforeunload", handleBeforeUnload);

    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload);
      // SPA navigation away. The user clicked another nav link or
      // back button — clean up the broker-side session so it doesn't
      // dangle until M4's session monitor reaps it.
      fireKeepalive();
    };
  }, [currentUser]);

  // ── Viewer event handlers ─────────────────────────────────

  // Stable callback identities — see M3-05's note on callback
  // identity NOT participating in the viewer's effect deps. These
  // do close over component state via setState dispatchers, which
  // are stable per React's contract.

  const handleConnect = useCallback(() => {
    setViewerState("connected");
  }, []);

  const handleDisconnect = useCallback((clean: boolean) => {
    // If we're mid-disconnect-mutation, the post-mutation handler
    // navigates; don't double-handle.
    if (disconnectFiredRef.current) return;

    if (clean) {
      // Server-side or graceful close (e.g. VM was shut down). Land
      // in the disconnected state and let the user decide to leave;
      // showing why the session ended is more useful than
      // auto-bouncing.
      setViewerState("disconnected");
      setErrorReason(null);
    } else {
      setViewerState("error");
      setErrorReason(
        "Connection lost unexpectedly. The remote desktop may have stopped.",
      );
    }
  }, []);

  const handleError = useCallback((reason: string) => {
    if (disconnectFiredRef.current) return;
    setViewerState("error");
    setErrorReason(reason);
  }, []);

  // ── Disconnect button ─────────────────────────────────────

  const handleDisconnectClick = useCallback(() => {
    const sessionId = sessionIdRef.current;
    if (sessionId === null) {
      // No live session to end — just navigate. Covers the
      // mutation-failure case where the user clicks "Back" after
      // an error.
      navigate("/desktops");
      return;
    }
    disconnectFiredRef.current = true;
    setViewerState("disconnecting");
    disconnectMutation.mutate(sessionId, {
      onSettled: () => {
        // Whether the broker DELETE succeeded or not, exit. The
        // mutation's onSettled invalidations already fired by the
        // time we get here.
        navigate("/desktops");
      },
    });
  }, [disconnectMutation, navigate]);

  const handleCtrlAltDel = useCallback(() => {
    viewerHandleRef.current?.sendCtrlAltDel();
  }, []);

  // ── Retry (after a connect-mutation error) ────────────────

  const handleRetry = useCallback(() => {
    if (poolId === undefined) return;
    setErrorReason(null);
    setViewerState("connecting");
    setConnectResult(null);
    connectMutation.mutate(poolId, {
      onSuccess: (result) => {
        setConnectResult(result);
      },
      onError: (error) => {
        setViewerState("error");
        setErrorReason(connectErrorMessage(error));
      },
    });
  }, [connectMutation, poolId]);

  // ── Derived: viewer ticket (narrow union to NoVNC) ────────

  const ticket: NoVNCTicketRead | null = useMemo(() => {
    if (connectResult === null) return null;
    if (connectResult.ticket.kind !== "novnc") {
      // v0 supports noVNC only. The broker only emits "novnc" today
      // but the type system requires us to acknowledge the union.
      // Tracked in the M3-02 exhaustiveness test
      // (`types.test-d.ts`).
      return null;
    }
    return connectResult.ticket;
  }, [connectResult]);

  // ── Render ────────────────────────────────────────────────

  // Page fills the viewport below the AppShell header (h-16 = 64px).
  // The arbitrary value `h-[calc(100vh-4rem)]` bypasses our
  // restricted theme (correctly — `[]` syntax is JIT). overflow-hidden
  // prevents scrollbars from peeking around the canvas.
  return (
    <div className="h-[calc(100vh-4rem)] flex flex-col overflow-hidden">
      <ConsoleToolbar
        state={viewerState}
        desktopName={connectResult?.desktop_name ?? null}
        errorReason={errorReason}
        onCtrlAltDel={handleCtrlAltDel}
        onDisconnect={handleDisconnectClick}
        busy={disconnectMutation.isPending}
      />

      <div className="flex-1 min-h-0 relative">
        {viewerState === "error" ? (
          <ErrorOverlay
            reason={errorReason ?? "An unknown error occurred."}
            onRetry={connectResult === null ? handleRetry : null}
            onBack={() => navigate("/desktops")}
            isRetrying={connectMutation.isPending}
          />
        ) : viewerState === "disconnected" ? (
          <DisconnectedOverlay onBack={() => navigate("/desktops")} />
        ) : ticket !== null ? (
          <NoVNCViewer
            ref={viewerHandleRef}
            ticket={ticket}
            onConnect={handleConnect}
            onDisconnect={handleDisconnect}
            onError={handleError}
          />
        ) : (
          <PreConnectOverlay />
        )}
      </div>
    </div>
  );
}

// ── Sub-components: pre-connect / disconnected / error ────────

function PreConnectOverlay() {
  return (
    <div className="absolute inset-0 flex items-center justify-center bg-black">
      <div className="flex flex-col items-center gap-3 text-text-inverse">
        <div
          className="w-8 h-8 border-2 border-action-primary border-t-transparent rounded-full animate-spin"
          aria-hidden
        />
        <p className="text-body">Brokering desktop…</p>
      </div>
    </div>
  );
}

interface DisconnectedOverlayProps {
  onBack: () => void;
}

function DisconnectedOverlay({ onBack }: DisconnectedOverlayProps) {
  return (
    <div className="absolute inset-0 flex items-center justify-center bg-bg p-6">
      <div className="text-center max-w-md">
        <h2 className="font-body text-h3 font-semibold text-text-primary">
          Disconnected
        </h2>
        <p className="text-body text-text-secondary mt-2">
          Your session has ended. Reconnect from the launcher when you're ready
          to continue.
        </p>
        <button
          type="button"
          onClick={onBack}
          className={
            "mt-6 inline-flex items-center gap-2 h-10 px-4 rounded-md " +
            "bg-action-primary text-text-on-accent text-body font-medium " +
            "transition-colors duration-fast ease-out " +
            "hover:bg-action-primary-hover " +
            "active:bg-action-primary-active " +
            "focus-visible:outline-none focus-visible:shadow-focus"
          }
        >
          <ArrowLeft size={16} strokeWidth={2} aria-hidden />
          Back to desktops
        </button>
      </div>
    </div>
  );
}

interface ErrorOverlayProps {
  reason: string;
  /** Null when there's no retry path (e.g. the viewer errored mid-session). */
  onRetry: (() => void) | null;
  onBack: () => void;
  isRetrying: boolean;
}

function ErrorOverlay({ reason, onRetry, onBack, isRetrying }: ErrorOverlayProps) {
  return (
    <div
      role="alert"
      className="absolute inset-0 flex items-center justify-center bg-bg p-6"
    >
      <div className="text-center max-w-md">
        <h2 className="font-body text-h3 font-semibold text-text-primary">
          Couldn't connect
        </h2>
        <p className="text-body text-text-secondary mt-2">{reason}</p>
        <div className="mt-6 flex items-center justify-center gap-3">
          <button
            type="button"
            onClick={onBack}
            className={
              "inline-flex items-center gap-2 h-10 px-4 rounded-md " +
              "bg-action-secondary text-action-secondary-text text-body font-medium " +
              "transition-colors duration-fast ease-out " +
              "hover:opacity-90 " +
              "focus-visible:outline-none focus-visible:shadow-focus"
            }
          >
            <ArrowLeft size={16} strokeWidth={2} aria-hidden />
            Back to desktops
          </button>
          {onRetry !== null && (
            <button
              type="button"
              onClick={onRetry}
              disabled={isRetrying}
              className={
                "inline-flex items-center gap-2 h-10 px-4 rounded-md " +
                "bg-action-primary text-text-on-accent text-body font-medium " +
                "transition-colors duration-fast ease-out " +
                "hover:bg-action-primary-hover " +
                "active:bg-action-primary-active " +
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
          )}
        </div>
      </div>
    </div>
  );
}

// ── Error message dispatch ────────────────────────────────────

/**
 * Map a connect-mutation error to a user-facing message.
 *
 * Mirrors M3-04's errorMessageFor pattern. When a third consumer
 * appears (M3-07?), lift this and M3-04's copy into lib/errors.ts.
 *
 * Per FE4: dispatch on `error.code`, not HTTP status. Two different
 * 503s — POOL_FULL and SERVICE_UNAVAILABLE — have different
 * remediation, and the broker codes are stable across HTTP statuses.
 */
function connectErrorMessage(error: Error): string {
  if (error instanceof BrokerError) {
    switch (error.code) {
      case "FORBIDDEN":
        return "You aren't entitled to this pool. Contact an administrator.";
      case "NOT_FOUND":
        return "This pool no longer exists. Return to your desktop list.";
      case "CONFLICT":
        return "This pool isn't accepting connections right now. Try again later or pick another pool.";
      case "POOL_FULL":
        return "All desktops in this pool are in use. Wait a moment and try again.";
      case "SERVICE_UNAVAILABLE":
        return "The hypervisor managing this pool is offline. Contact an administrator.";
      case "PROVIDER_ERROR":
        return "The hypervisor returned an error. Try again, or contact an administrator if it persists.";
      case "PROVIDER_TIMEOUT":
        return "The hypervisor took too long to respond. Try again.";
      case "UNAUTHORIZED":
        return "Your session has expired. Sign out and back in to continue.";
      case "INTERNAL_ERROR":
      case "ERROR":
      default:
        return "Something went wrong connecting to the desktop. Try again, or contact an administrator if it persists.";
    }
  }
  return "Couldn't reach the broker. Check your connection and try again.";
}
