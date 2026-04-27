export type SessionStatus =
  | "connecting"
  | "active"
  | "disconnected"
  | "ended";

/**
 * Mirror of broker `app.schemas.user.UserSessionView`.
 *
 * `desktop_id`, `desktop_name`, `pool_id`, `pool_name` are nullable —
 * the M2-15-fix-2 FK on `sessions.desktop_id` is `ON DELETE SET NULL`,
 * so a row whose desktop has been destroyed surfaces here with these
 * fields set to null. The session-side fields (protocol, timestamps,
 * status) survive the destroy. M3-07 renders "(desktop deleted)"
 * for the orphan case rather than blanking the row.
 *
 * `pool_name` is the pool's `display_name` (the friendly one), per
 * the broker handler in `app/api/user.py` → `list_user_sessions`.
 * Same naming gotcha as `UserPoolView.display_name` — render this
 * directly; do not look up `UserPoolView.name`.
 */
export interface UserSessionView {
  id: string;
  desktop_id: string | null;
  desktop_name: string | null;
  pool_id: string | null;
  pool_name: string | null;
  protocol: string;  // "novnc" in v0
  status: SessionStatus;
  connected_at: string | null;
  disconnected_at: string | null;
  ended_at: string | null;
}
