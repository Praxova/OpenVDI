import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "./AuthContext";

/**
 * Layout-route wrapper. Used as `element={<ProtectedRoute />}` on a
 * parent route in the route table; child routes render inside via
 * `<Outlet />`. The AppShell is a child route element, so it inherits
 * the auth gate without each page having to check.
 *
 * On redirect we pass the current pathname in `state.from` so the
 * login page can bounce back after a successful login. `replace`
 * rewrites the history entry instead of pushing — clicking Back from
 * the login screen should not return the user to the protected URL
 * they were just bounced from.
 */
export function ProtectedRoute() {
  const { currentUser } = useAuth();
  const location = useLocation();

  if (currentUser === null) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <Outlet />;
}
