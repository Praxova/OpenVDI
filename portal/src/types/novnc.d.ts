/**
 * Minimal ambient module declaration for @novnc/novnc v1.4+.
 *
 * The package ships JavaScript only at this version line; this file
 * narrows the surface to what NoVNCViewer.tsx uses, with strict
 * types for each method's args and event detail shapes.
 *
 * If we ever import additional API surface (clipboard pass-through,
 * keyboard layout selection, etc.), add to this declaration rather
 * than reaching for `any`.
 *
 * Reference: https://github.com/novnc/noVNC/blob/v1.4.0/core/rfb.js
 */
declare module "@novnc/novnc" {
  export interface RFBOptions {
    credentials?: { username?: string; password?: string; target?: string };
    shared?: boolean;
    repeaterID?: string;
    wsProtocols?: string[];
  }

  /** noVNC's `disconnect` event detail. */
  export interface RFBDisconnectEventDetail {
    clean: boolean;
  }

  /** noVNC's `securityfailure` event detail. */
  export interface RFBSecurityFailureEventDetail {
    status: number;
    reason: string;
  }

  /** noVNC's `credentialsrequired` event detail. */
  export interface RFBCredentialsRequiredEventDetail {
    types: string[];
  }

  /**
   * The RFB class. Extends EventTarget — listeners are attached via
   * addEventListener / removeEventListener.
   *
   * Event names actually emitted (subset relevant to the viewer):
   *   "connect"               — connection established
   *   "disconnect"            — disconnect event with detail.clean
   *   "securityfailure"       — auth/security handshake failed
   *   "credentialsrequired"   — server requested creds we didn't supply
   *
   * Other events exist (clipboard, bell, capabilities, desktopname,
   * resize) but are not used by the v0 viewer.
   */
  export default class RFB extends EventTarget {
    constructor(
      target: HTMLElement,
      urlOrWebSocket: string | WebSocket,
      options?: RFBOptions,
    );

    /**
     * If true, the canvas scales to fit the container size. Set after
     * construction. Default false in noVNC v1.4.
     */
    scaleViewport: boolean;

    /**
     * If true, the noVNC client requests the server resize its
     * framebuffer to the container size. Requires the VNC server to
     * support the ExtendedDesktopSize pseudo-encoding. Default false.
     */
    resizeSession: boolean;

    /** Send a clean disconnect on the wire and tear down the websocket. */
    disconnect(): void;

    /** Send Ctrl+Alt+Del to the remote desktop. */
    sendCtrlAltDel(): void;

    /** Send Ctrl+Alt+F1..F12 (passed as keysym). */
    sendKey(keysym: number, code: string, down?: boolean): void;
  }
}
