import { useState } from "react";
import { Moon, Sun } from "lucide-react";

import { resolveTheme, toggleTheme, type Theme } from "@/lib/theme";

interface ThemeToggleProps {
  className?: string;
}

/**
 * Light/dark toggle. Single button, swaps icon based on the active
 * theme. Persists user choice via lib/theme.ts.
 *
 * Stroke width 1.5 matches `--icon-stroke-width-default` from the
 * design system for `--icon-md` size. Lucide's default is 2; setting
 * it explicitly keeps visual rhythm consistent with other Praxova
 * portals.
 *
 * `focus-visible:shadow-focus` references the design system's
 * canonical focus ring (gold halo at 35% opacity, swapping to
 * gold-light at 45% under dark mode automatically via tokens.css).
 */
export function ThemeToggle({ className = "" }: ThemeToggleProps) {
  const [theme, setLocalTheme] = useState<Theme>(() => resolveTheme());

  const handleClick = () => {
    setLocalTheme(toggleTheme());
  };

  const isDark = theme === "dark";
  const Icon = isDark ? Sun : Moon;
  const label = isDark ? "Switch to light mode" : "Switch to dark mode";

  return (
    <button
      type="button"
      onClick={handleClick}
      aria-label={label}
      title={label}
      className={
        "inline-flex items-center justify-center " +
        "rounded-md p-2 text-text-primary " +
        "transition-colors duration-fast ease-out " +
        "hover:bg-surface-2 " +
        "focus-visible:outline-none focus-visible:shadow-focus " +
        className
      }
    >
      <Icon size={20} strokeWidth={1.5} aria-hidden />
    </button>
  );
}
