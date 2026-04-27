import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
} from "react";

import RFB from "@novnc/novnc";
import type {
  RFBDisconnectEventDetail,
  RFBSecurityFailureEventDetail,
} from "@novnc/novnc";

import type { NoVNCTicketRead } from "@/types";

export interface NoVNCViewerHandle {
  sendCtrlAltDel: () => void;
}

export interface NoVNCViewerProps {
  ticket: NoVNCTicketRead;
  onConnect?: () => void;
  onDisconnect?: (clean: boolean) => void;
  onError?: (reason: string) => void;
  className?: string;
}

/**
 * NoVNC-backed VM console viewer.
 *
 * Pure presentational: ticket in, callbacks out, plus an imperative
 * handle for sendCtrlAltDel. The viewer owns NO fetch, routing, or
 * auth concerns — those live in M3-06's ConsolePage.
 *
 * Lifecycle (per the M3-05 prompt's *RFB lifecycle, StrictMode, and
 * the canvas-stacking trap* section):
 *   1. Effect runs when `ticket` reference changes.
 *   2. Container is cleared (replaceChildren) — defends against
 *      StrictMode double-mount stacking canvases.
 *   3. RFB is constructed with the ticket's URL and password.
 *   4. Event listeners attached for connect / disconnect /
 *      securityfailure / credentialsrequired.
 *   5. Cleanup: detach listeners, rfb.disconnect(), clear container.
 *
 * Callback identity does NOT participate in the effect deps — the
 * callbacks are read through a ref that's updated every render.
 * Otherwise a parent re-rendering with new function identities would
 * trigger spurious teardown+reconstruct cycles.
 *
 * StrictMode safety: PVE's vncproxy ticket has a TTL (~30s default)
 * during which multiple WebSocket connects are accepted. The dev
 * double-mount therefore tears down cleanly and reconstructs against
 * the same not-yet-expired ticket. If a future provider issues
 * strictly single-use tickets, this assumption breaks; symptom is
 * dev-mode immediate disconnect after first paint while prod (no
 * StrictMode) works. Mitigation in that case would be to either
 * mint two tickets at connect time or to suppress StrictMode for
 * the console route.
 */
export const NoVNCViewer = forwardRef<NoVNCViewerHandle, NoVNCViewerProps>(
  function NoVNCViewer(
    { ticket, onConnect, onDisconnect, onError, className = "" },
    ref,
  ) {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const rfbRef = useRef<RFB | null>(null);

    // Ref-mirror the callbacks so identity changes don't re-trigger
    // the effect. The effect reads `callbacksRef.current.X` inside
    // its handlers; any update before the next event fires is seen.
    const callbacksRef = useRef({ onConnect, onDisconnect, onError });
    callbacksRef.current = { onConnect, onDisconnect, onError };

    useImperativeHandle(
      ref,
      () => ({
        sendCtrlAltDel: () => {
          // Safe no-op when no live connection (e.g. button clicked
          // after disconnect); rfb.sendCtrlAltDel internally checks
          // its own connection state.
          rfbRef.current?.sendCtrlAltDel();
        },
      }),
      [],
    );

    useEffect(() => {
      const container = containerRef.current;
      if (container === null) return;

      // Defend against canvas-stacking on StrictMode double-mount
      // and on ticket-driven re-runs.
      container.replaceChildren();

      let didError = false;

      const rfb = new RFB(container, ticket.websocket_url, {
        credentials: { password: ticket.password },
        wsProtocols: ["binary"],
        shared: true,
      });

      // Scale the canvas to the container; do not request server-side
      // framebuffer resize (the VM's resolution is fixed for v0).
      // M5+ may flip resizeSession to true when guests reliably
      // support ExtendedDesktopSize.
      rfb.scaleViewport = true;
      rfb.resizeSession = false;

      rfbRef.current = rfb;

      const handleConnect = () => {
        callbacksRef.current.onConnect?.();
      };

      const handleDisconnect = (e: Event) => {
        // Per the type declaration, the disconnect event is a
        // CustomEvent<RFBDisconnectEventDetail>.
        const detail = (e as CustomEvent<RFBDisconnectEventDetail>).detail;
        const clean = detail?.clean ?? false;
        callbacksRef.current.onDisconnect?.(clean);
      };

      const handleSecurityFailure = (e: Event) => {
        const detail = (e as CustomEvent<RFBSecurityFailureEventDetail>).detail;
        const reason = detail?.reason ?? "VNC security handshake failed";
        didError = true;
        callbacksRef.current.onError?.(reason);
      };

      const handleCredentialsRequired = () => {
        // Should never reach here — we always supply a password from
        // the ticket. If it does, treat as an error: the server
        // wants creds we didn't provide.
        didError = true;
        callbacksRef.current.onError?.(
          "Remote desktop requested additional credentials",
        );
      };

      rfb.addEventListener("connect", handleConnect);
      rfb.addEventListener("disconnect", handleDisconnect);
      rfb.addEventListener("securityfailure", handleSecurityFailure);
      rfb.addEventListener("credentialsrequired", handleCredentialsRequired);

      return () => {
        rfb.removeEventListener("connect", handleConnect);
        rfb.removeEventListener("disconnect", handleDisconnect);
        rfb.removeEventListener("securityfailure", handleSecurityFailure);
        rfb.removeEventListener(
          "credentialsrequired",
          handleCredentialsRequired,
        );

        // disconnect() is idempotent — safe to call even if the
        // websocket never opened (e.g. the cleanup races against an
        // initial securityfailure).
        try {
          rfb.disconnect();
        } catch {
          // noVNC has been observed to throw inside disconnect when
          // the websocket is mid-handshake. Swallow — we're tearing
          // down anyway, and there's no recovery action.
        }

        // Clear the canvas the disconnect leaves behind. Defensive:
        // the next mount also clears, but doing it here too means a
        // genuine unmount leaves no stray DOM.
        container.replaceChildren();

        rfbRef.current = null;

        // didError is referenced here only to keep its declaration
        // observable to the cleanup; its main job is annotating the
        // disconnect callback. No-op assignment to silence
        // unused-variable lint while documenting intent.
        void didError;
      };
    }, [ticket]);

    return (
      <div
        ref={containerRef}
        className={
          // Black bg under the canvas so any letterboxing reads as
          // intentional (TVs / displays / vmware all do this).
          // Focus styling on the outer div lives at the page level
          // (M3-06's ConsoleToolbar handles focus orchestration).
          "w-full h-full bg-black " + className
        }
        // Make the container focusable so keyboard input flows to the
        // RFB-injected canvas — RFB attaches its key listeners to the
        // canvas, but the page can also imperatively focus the
        // container if a future panel takes focus away.
        tabIndex={0}
        // The viewer is a "live region" of sorts but conventional
        // screen-reader semantics don't apply to a remote desktop.
        // Marking aria-hidden lets assistive tech skip the canvas
        // (it has no semantic content); the toolbar in M3-06 carries
        // the announcing surface.
        aria-hidden
      />
    );
  },
);

NoVNCViewer.displayName = "NoVNCViewer";
