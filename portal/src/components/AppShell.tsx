import { LogOut } from "lucide-react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";

import { AdminMenu } from "./AdminMenu";
import { BrandMark } from "./BrandMark";
import { ThemeToggle } from "./ThemeToggle";
import { useAuth } from "@/auth/AuthContext";

const NAV_LINKS = [
  { to: "/desktops", label: "Desktops" },
  { to: "/sessions", label: "Sessions" },
] as const;

/**
 * Top-bar header + main content area. Used as the layout-route
 * element for the protected branch; child routes render in <Outlet />.
 *
 * Per design-system.md §8.10.1: 64px tall, surface-1 background,
 * 1px border-bottom in border-subtle, padding space-4 / space-6.
 * Brand on left, nav center-left, theme toggle + logout on right.
 *
 * `queryClient.clear()` on logout prevents a different user logging
 * in on the same tab from briefly seeing the previous user's
 * `/me/desktops` cached results before the fresh fetch lands.
 */
export function AppShell() {
  const { state, logout } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const user = state.status === "authenticated" ? state.user : null;
  const isAdmin = user?.role === "admin";

  const handleLogout = async () => {
    await logout();
    queryClient.clear();
    navigate("/login", { replace: true });
  };

  return (
    <div className="min-h-screen flex flex-col bg-bg text-text-primary">
      <header
        className={
          "sticky top-0 z-sticky " +
          "h-16 flex items-center justify-between " +
          "px-6 py-4 " +
          "bg-surface-1 border-b border-border-subtle"
        }
      >
        <div className="flex items-center gap-6">
          <span className="flex items-center gap-3">
            <BrandMark size={28} />
            <span className="font-display text-h4 font-semibold text-text-primary">
              OpenVDI
            </span>
          </span>

          <nav className="flex items-center gap-2">
            {NAV_LINKS.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                className={({ isActive }) =>
                  "px-3 py-2 rounded-md text-body font-medium " +
                  "transition-colors duration-fast ease-out " +
                  (isActive
                    ? "bg-surface-2 text-text-primary"
                    : "text-text-secondary hover:text-text-primary hover:bg-surface-2")
                }
              >
                {link.label}
              </NavLink>
            ))}
            {isAdmin && <AdminMenu />}
          </nav>
        </div>

        <div className="flex items-center gap-2">
          <span className="text-body-sm text-text-secondary mr-2">
            {user?.username ?? ""}
          </span>
          <ThemeToggle />
          <button
            type="button"
            onClick={handleLogout}
            aria-label="Log out"
            title="Log out"
            className={
              "inline-flex items-center justify-center " +
              "rounded-md p-2 text-text-primary " +
              "transition-colors duration-fast ease-out " +
              "hover:bg-surface-2 " +
              "focus-visible:outline-none focus-visible:shadow-focus"
            }
          >
            <LogOut size={20} strokeWidth={1.5} aria-hidden />
          </button>
        </div>
      </header>

      <main className="flex-1">
        <Outlet />
      </main>
    </div>
  );
}
