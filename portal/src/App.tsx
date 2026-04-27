import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/AppShell";
import { ConsolePage } from "@/pages/ConsolePage";
import { DesktopsPage } from "@/pages/DesktopsPage";
import { LoginPage } from "@/pages/LoginPage";
import { SessionsPage } from "@/pages/SessionsPage";
import { ProtectedRoute } from "@/auth/ProtectedRoute";

/**
 * Route layout:
 *
 *   /login                    — public, dev-auth form
 *   /                         — protected, redirects to /desktops
 *   /desktops                 — protected, launcher (M3-04)
 *   /desktops/:poolId/console — protected, console (M3-06)
 *   /sessions                 — protected, sessions (M3-07)
 *   *                         — protected, redirects to /desktops
 *
 * The protected branch is wrapped in <ProtectedRoute> + <AppShell>.
 * Pages render inside AppShell's <Outlet />.
 */
export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />

        <Route element={<ProtectedRoute />}>
          <Route element={<AppShell />}>
            <Route path="/" element={<Navigate to="/desktops" replace />} />
            <Route path="/desktops" element={<DesktopsPage />} />
            <Route
              path="/desktops/:poolId/console"
              element={<ConsolePage />}
            />
            <Route path="/sessions" element={<SessionsPage />} />
            <Route path="*" element={<Navigate to="/desktops" replace />} />
          </Route>
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
