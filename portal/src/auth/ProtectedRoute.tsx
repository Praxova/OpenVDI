import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "./AuthContext";
import { InitializingScreen } from "@/components/InitializingScreen";

/**
 * Layout-route wrapper. Used as `element={<ProtectedRoute />}` on a
 * parent route in the route table; child routes render inside via
 * `<Outlet />`. The AppShell is a child route element, so it inherits
 * the auth gate without each page having to check.
 *
 * Three branches per AuthState:
 *   initializing   — render the InitializingScreen until refresh resolves.
 *   unauthenticated — bounce to /login, capturing pathname for post-login redirect.
 *   authenticated  — render the child outlet.
 */
export function ProtectedRoute() {
  const { state } = useAuth();
  const location = useLocation();

  if (state.status === "initializing") {
    return <InitializingScreen />;
  }
  if (state.status === "unauthenticated") {
    return (
      <Navigate to="/login" replace state={{ from: location.pathname }} />
    );
  }
  return <Outlet />;
}
