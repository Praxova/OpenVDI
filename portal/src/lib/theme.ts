/**
 * Theme handling for the OpenVDI portal.
 *
 * The Praxova design system supports two modes: light (default) and
 * dark (opt-in via `<html data-theme="dark">`). The portal honors:
 *
 *   1. An explicit user choice persisted to localStorage. If present,
 *      it wins.
 *   2. `prefers-color-scheme` from the browser. Used as the default
 *      when the user has not toggled.
 *
 * Notably, the portal does NOT write a localStorage value for the
 * default. Only an explicit toggle persists. This keeps the "follow
 * my OS" behavior intact for users who never click the toggle, even
 * if their OS preference changes later.
 *
 * Per design-system.md §10.1: read prefers-color-scheme on first
 * visit, persist user choice on subsequent visits.
 */

export type Theme = "light" | "dark";

const STORAGE_KEY = "openvdi.theme";

/** Read the user's persisted preference, if any. */
export function readPersistedTheme(): Theme | null {
  // Wrap in try/catch — localStorage can throw under tightened browser
  // privacy modes (Safari "Block all cookies", some mobile browsers).
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === "light" || raw === "dark") return raw;
    return null;
  } catch {
    return null;
  }
}

/** Persist or clear the user's explicit choice. */
export function writePersistedTheme(theme: Theme | null): void {
  try {
    if (theme === null) {
      window.localStorage.removeItem(STORAGE_KEY);
    } else {
      window.localStorage.setItem(STORAGE_KEY, theme);
    }
  } catch {
    // Silently ignore — the apply step still updates the DOM, the
    // user's session is functional, just not persisted.
  }
}

/** Read prefers-color-scheme. Defaults to "light" if matchMedia is absent. */
export function readSystemTheme(): Theme {
  if (typeof window === "undefined" || !window.matchMedia) return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

/** Resolve which theme should be active right now. */
export function resolveTheme(): Theme {
  return readPersistedTheme() ?? readSystemTheme();
}

/**
 * Apply a theme to the DOM. Idempotent.
 *
 * Light = remove the data-theme attribute (the design tokens.css uses
 * :root for light defaults; an explicit data-theme="light" would also
 * match :root but adds nothing — keep the DOM minimal).
 */
export function applyTheme(theme: Theme): void {
  if (typeof document === "undefined") return;
  if (theme === "dark") {
    document.documentElement.setAttribute("data-theme", "dark");
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
}

/**
 * Synchronous theme initialization. Called from `main.tsx` BEFORE
 * `createRoot().render()` so the data-theme attribute is on `<html>`
 * before the first paint. Without this, users see a 50-200ms flash
 * of the wrong palette on every page load.
 *
 * Returns the resolved theme so the caller can avoid re-resolving
 * if it needs the value.
 */
export function initTheme(): Theme {
  const theme = resolveTheme();
  applyTheme(theme);
  return theme;
}

/**
 * Toggle and persist. Returns the new theme.
 *
 * Always persists — once the user toggles, their explicit choice is
 * locked in regardless of OS preference changes.
 */
export function toggleTheme(): Theme {
  const next: Theme = resolveTheme() === "dark" ? "light" : "dark";
  writePersistedTheme(next);
  applyTheme(next);
  return next;
}

/**
 * Set explicitly. `"system"` clears the persisted preference and
 * resolves against the OS. Used by tests + future "system default" UI;
 * the M3-03 ThemeToggle does not call it.
 */
export function setTheme(theme: Theme | "system"): Theme {
  if (theme === "system") {
    writePersistedTheme(null);
    const resolved = readSystemTheme();
    applyTheme(resolved);
    return resolved;
  }
  writePersistedTheme(theme);
  applyTheme(theme);
  return theme;
}
