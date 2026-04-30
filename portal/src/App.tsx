import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/AppShell";
import { ConsolePage } from "@/pages/ConsolePage";
import { DesktopsPage } from "@/pages/DesktopsPage";
import { LoginPage } from "@/pages/LoginPage";
import { SessionsPage } from "@/pages/SessionsPage";
import {
  AdminPlaceholder,
  ClusterFormPage,
  ClustersPage,
  DashboardPage,
  TemplateFormPage,
  TemplatesPage,
} from "@/pages/admin";
import { AdminRoute } from "@/auth/AdminRoute";
import { ProtectedRoute } from "@/auth/ProtectedRoute";

/**
 * Route layout:
 *
 *   /login                    — public, LDAP/JWT sign-in
 *   /                         — protected, redirects to /desktops
 *   /desktops                 — protected, launcher
 *   /desktops/:poolId/console — protected, console
 *   /sessions                 — protected, sessions
 *   /admin/*                  — protected + admin-gated, 7 placeholders
 *                               (each replaced by M4-18..M4-24)
 *   *                         — protected, redirects to /desktops
 *
 * The protected branch is wrapped in <ProtectedRoute> + <AppShell>.
 * Admin routes nest inside an <AdminRoute> that asserts role=admin.
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

            <Route element={<AdminRoute />}>
              <Route path="/admin" element={<DashboardPage />} />
              <Route path="/admin/clusters" element={<ClustersPage />} />
              <Route
                path="/admin/clusters/new"
                element={<ClusterFormPage />}
              />
              <Route
                path="/admin/clusters/:id/edit"
                element={<ClusterFormPage />}
              />
              <Route path="/admin/templates" element={<TemplatesPage />} />
              <Route
                path="/admin/templates/new"
                element={<TemplateFormPage />}
              />
              <Route
                path="/admin/templates/:id/edit"
                element={<TemplateFormPage />}
              />
              <Route
                path="/admin/pools"
                element={<AdminPlaceholder title="Pools" comingIn="M4-21" />}
              />
              <Route
                path="/admin/desktops"
                element={
                  <AdminPlaceholder title="Desktops (admin)" comingIn="M4-22" />
                }
              />
              <Route
                path="/admin/sessions"
                element={
                  <AdminPlaceholder title="Sessions (admin)" comingIn="M4-23" />
                }
              />
              <Route
                path="/admin/audit"
                element={<AdminPlaceholder title="Audit Log" comingIn="M4-24" />}
              />
            </Route>

            <Route path="*" element={<Navigate to="/desktops" replace />} />
          </Route>
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
