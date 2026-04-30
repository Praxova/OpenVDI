import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { NavLink } from "react-router-dom";

interface AdminLink {
  to: string;
  label: string;
}

const ADMIN_LINKS: AdminLink[] = [
  { to: "/admin",           label: "Dashboard" },
  { to: "/admin/clusters",  label: "Clusters" },
  { to: "/admin/templates", label: "Templates" },
  { to: "/admin/pools",     label: "Pools" },
  { to: "/admin/desktops",  label: "Desktops" },
  { to: "/admin/sessions",  label: "Sessions" },
  { to: "/admin/audit",     label: "Audit Log" },
];

/**
 * Admin navigation dropdown. Conditionally rendered by AppShell
 * (only for admin users). Closed by default; click to open.
 *
 * Keyboard support:
 *   - Tab into the button → Enter/Space toggles open
 *   - Tab through menu items when open (each is a focusable NavLink)
 *   - Escape closes and refocuses the button
 *   - Click outside closes
 *
 * v0 limitation: no Arrow Up/Down navigation between menu items.
 * Tab-cycle works because items are NavLinks; users with screen
 * readers or arrow-key habits can navigate via standard browser tab
 * traversal. M5+ may swap in a headless library (Radix DropdownMenu,
 * React Aria) for full WAI-ARIA Authoring Practices keyboard support.
 */
export function AdminMenu() {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const close = useCallback(() => setOpen(false), []);

  // Click outside closes. Check both refs — without checking buttonRef,
  // the button click bubbles to document and immediately re-closes.
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      if (menuRef.current?.contains(target)) return;
      if (buttonRef.current?.contains(target)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  // Escape closes and refocuses the button.
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open]);

  return (
    <div className="relative">
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        className={
          "inline-flex items-center gap-1 px-3 py-2 rounded-md " +
          "text-body font-medium " +
          "text-text-secondary hover:text-text-primary hover:bg-surface-2 " +
          "transition-colors duration-fast ease-out " +
          "focus-visible:outline-none focus-visible:shadow-focus"
        }
      >
        Admin
        <ChevronDown
          size={16}
          strokeWidth={2}
          aria-hidden
          className={
            "transition-transform duration-fast ease-out " +
            (open ? "rotate-180" : "")
          }
        />
      </button>

      {open && (
        <div
          ref={menuRef}
          role="menu"
          aria-label="Admin navigation"
          className={
            "absolute top-full left-0 mt-1 min-w-[12rem] z-popover " +
            "bg-surface-1 border border-border-subtle rounded-md " +
            "shadow-md py-1"
          }
        >
          {ADMIN_LINKS.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              role="menuitem"
              onClick={close}
              end={link.to === "/admin"}
              className={({ isActive }) =>
                "block px-3 py-2 text-body-sm " +
                "transition-colors duration-fast ease-out " +
                "focus-visible:outline-none focus-visible:bg-surface-2 " +
                (isActive
                  ? "bg-surface-2 text-text-primary font-medium"
                  : "text-text-secondary hover:text-text-primary hover:bg-surface-2")
              }
            >
              {link.label}
            </NavLink>
          ))}
        </div>
      )}
    </div>
  );
}
