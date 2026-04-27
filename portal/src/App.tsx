import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/AppShell";
import { LoginPage } from "@/pages/LoginPage";
import { ProtectedRoute } from "@/auth/ProtectedRoute";

/**
 * Route layout:
 *
 *   /login                    — public, dev-auth form
 *   /                         — protected, redirects to /desktops
 *   /desktops                 — protected, M3-04 launcher (placeholder until then)
 *   /desktops/:poolId/console — protected, M3-06 console (NOT registered here)
 *   /sessions                 — protected, M3-07 sessions (placeholder until then)
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
            <Route
              path="/desktops"
              element={
                <PlaceholderPage
                  title="Desktops"
                  hint="The launcher arrives in M3-04."
                />
              }
            />
            <Route
              path="/sessions"
              element={
                <PlaceholderPage
                  title="Sessions"
                  hint="The sessions view arrives in M3-07."
                />
              }
            />
            <Route path="*" element={<Navigate to="/desktops" replace />} />
          </Route>
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

interface PlaceholderPageProps {
  title: string;
  hint: string;
}

/**
 * Inline placeholder used for /desktops and /sessions until M3-04 and
 * M3-07 land. Once those prompts replace the routes with real pages,
 * this component is unused and should be deleted as part of the
 * cleanup in whichever prompt removes the last reference.
 */
function PlaceholderPage({ title, hint }: PlaceholderPageProps) {
  return (
    <div className="p-6">
      <section className="max-w-2xl bg-surface-1 border border-border-subtle rounded-lg p-6">
        <h1 className="font-display text-h2 font-semibold text-text-primary">
          {title}
        </h1>
        <p className="text-body text-text-secondary mt-2">{hint}</p>
      </section>
    </div>
  );
}
