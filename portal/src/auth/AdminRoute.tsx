import { Navigate, Outlet } from "react-router-dom";

import { useAuth } from "./AuthContext";

/**
 * Layout-route wrapper that asserts the user is authenticated AND
 * has admin role. Mount INSIDE <ProtectedRoute> in the route tree —
 * AdminRoute relies on the parent having already gated against
 * unauthenticated users.
 *
 * Non-admin users hitting an admin URL bounce to /desktops, NOT
 * /login. They're authenticated; login wouldn't help. Per FE2.
 *
 * Defensive: if the parent ProtectedRoute somehow let through an
 * unauthenticated user (shouldn't happen, but the type system
 * doesn't enforce route-tree invariants), bounce to /login. The
 * `state.status !== "authenticated"` check is the belt + suspenders.
 */
export function AdminRoute() {
  const { state } = useAuth();

  if (state.status !== "authenticated") {
    return <Navigate to="/login" replace />;
  }

  if (state.user.role !== "admin") {
    return <Navigate to="/desktops" replace />;
  }

  return <Outlet />;
}
