/**
 * User identity exposed by the auth layer.
 *
 * Per FE8 in the M3 seed: the shape of `DevUser` is identical to the
 * decoded payload that M4's JWT-based auth will produce. Only the
 * source of truth changes (header form vs decoded JWT). Pages,
 * `BrokerClientProvider`, and `ProtectedRoute` consume `DevUser`
 * through `useAuth()` and don't change in M4.
 */

export type Role = "admin" | "user";

export interface DevUser {
  username: string;
  groups: string[];
  role: Role;
}

export interface AuthState {
  /** The currently logged-in user, or null if logged out. */
  currentUser: DevUser | null;
  /** Persist a user and become "logged in". */
  login: (user: DevUser) => void;
  /** Clear the user and become "logged out". */
  logout: () => void;
}
