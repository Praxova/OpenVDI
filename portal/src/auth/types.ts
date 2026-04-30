export type Role = "admin" | "user";

export interface User {
  username: string;
  groups: string[];
  role: Role;
}

/**
 * Discriminated-union auth state. ProtectedRoute / pages branch on
 * `state.status` to decide what to render.
 *
 *   initializing   — refresh-on-mount in flight; render a placeholder.
 *   authenticated  — have an access token + decoded claims.
 *   unauthenticated — refresh failed or never logged in; bounce to /login.
 */
export type AuthState =
  | { status: "initializing" }
  | {
      status: "authenticated";
      user: User;
      accessToken: string;
      expiresAt: number; // epoch ms
    }
  | { status: "unauthenticated" };

export interface AuthContextValue {
  state: AuthState;
  /** Get the current access token, or null if not authenticated. */
  getAccessToken: () => string | null;
  /** POST /auth/login with credentials. Throws LoginError on
      invalid credentials (caller renders the error). */
  login: (username: string, password: string) => Promise<void>;
  /** Trigger a refresh. De-duped: concurrent callers share the
      promise. Returns the new access token, or null on failure. */
  refresh: () => Promise<string | null>;
  /** POST /auth/logout, then clear local state. */
  logout: () => Promise<void>;
}
