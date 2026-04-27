// ── Pool / desktop status enums ──────────────────────────────

export type PoolType = "persistent" | "nonpersistent";

export type PoolStatus =
  | "active"
  | "disabled"
  | "provisioning"
  | "error"
  | "draining"
  | "deleting";

export type DesktopStatus =
  | "provisioning"
  | "available"
  | "assigned"
  | "connected"
  | "disconnected"
  | "error"
  | "deleting"
  | "maintenance";

// ── User-facing views (GET /me/desktops) ─────────────────────

/**
 * Mirror of broker `app.schemas.user.UserDesktopView`.
 *
 * `name` is the VM name (e.g. "ENG-003"). It IS user-facing — operators
 * reference it across the portal, the Proxmox UI, and the audit log.
 * Not to be confused with `UserPoolView.name` (a slug, internal-only).
 */
export interface UserDesktopView {
  id: string;
  name: string;
  status: DesktopStatus;
  power_state: string;            // "running" | "stopped" | "paused" | "unknown"
  last_connected: string | null;  // ISO 8601
}

/**
 * Mirror of broker `app.schemas.user.UserPoolView`.
 *
 * Naming convention pinned across M3:
 *   - `id`           : UUID. Used in URLs, never rendered.
 *   - `name`         : slug, [a-z0-9_-]. INTERNAL. NEVER rendered to users.
 *   - `display_name` : human-facing string. ALWAYS the visible label.
 *
 * VMware Horizon uses the same separation (pool_id slug + display name).
 * Operators familiar with Horizon will recognize the pattern; everyone
 * else should treat `display_name` as the only user-visible string.
 */
export interface UserPoolView {
  id: string;
  name: string;
  display_name: string;
  description: string | null;
  pool_type: PoolType;
  status: PoolStatus;
  assigned_desktop: UserDesktopView | null;
}

// ── Console tickets (POST /me/desktops/{pool_id}/connect) ────

export type ConsoleKind = "novnc" | "spice" | "webmks" | "rdp";

/**
 * v0 produces only `kind: "novnc"`. The other variants are defined so
 * the discriminated-union narrowing carries weight at compile time:
 * adding a new `case` to the renderer dispatch in M3-06 (or later) is
 * mechanical; forgetting one is a TypeScript error.
 */
export interface NoVNCTicketRead {
  kind: "novnc";
  websocket_url: string;
  password: string;
  cert_pem: string | null;
}

export interface SpiceTicketRead {
  kind: "spice";
  host: string;
  port: number;
  tls_port: number | null;
  password: string;
  proxy: string | null;
}

export interface WebMKSTicketRead {
  kind: "webmks";
  host: string;
  port: number;
  ticket: string;
}

export interface RDPTicketRead {
  kind: "rdp";
  host: string;
  port: number;  // defaults to 3389 server-side; surfaced explicitly here
  username: string | null;
  password: string | null;
  gateway_host: string | null;
  gateway_token: string | null;
}

export type ConsoleTicketRead =
  | NoVNCTicketRead
  | SpiceTicketRead
  | WebMKSTicketRead
  | RDPTicketRead;

/**
 * Mirror of broker `app.schemas.connect.ConnectResponse`.
 */
export interface ConnectResponse {
  session_id: string;
  desktop_name: string;
  ticket: ConsoleTicketRead;
}
