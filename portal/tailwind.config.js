/** @type {import('tailwindcss').Config} */
//
// Tailwind v3 + Praxova design-token theme bridge (ST6).
//
// We REPLACE Tailwind's default palette/spacing/radius/shadow/typography
// scales with mappings to the design-system CSS custom properties from
// src/styles/praxova/tokens.css. After this bridge:
//   bg-action-primary   → background-color: var(--color-action-primary)
//   p-4                 → padding: var(--space-4)
//   rounded-md          → border-radius: var(--radius-md)
//   shadow-md           → box-shadow: var(--shadow-2)
//   font-body           → font-family: var(--font-body)
//   text-h1             → font-size: var(--text-h1-size) (+ line-height + tracking)
//
// Vanilla utilities like bg-amber-500, p-3.5, rounded-2xl, text-xl
// DO NOT compile — a developer reaching for them gets an emitted-but-
// unstyled class rather than silently bypassing the design system.
//
// If a future component needs a token not yet bridged, add it here with
// a comment naming the consumer. Do NOT reach for `extend.colors` to
// reintroduce vanilla Tailwind shades.

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],

  theme: {
    // Wipe Tailwind's color palette. Only design-system roles compile.
    colors: {
      transparent: "transparent",
      current: "currentColor",
      inherit: "inherit",

      // Surfaces
      "bg":               "var(--color-bg)",
      "surface-1":        "var(--color-surface-1)",
      "surface-2":        "var(--color-surface-2)",
      "surface-3":        "var(--color-surface-3)",
      "surface-overlay":  "var(--color-surface-overlay)",

      // Text
      "text-primary":     "var(--color-text-primary)",
      "text-secondary":   "var(--color-text-secondary)",
      "text-tertiary":    "var(--color-text-tertiary)",
      "text-disabled":    "var(--color-text-disabled)",
      "text-inverse":     "var(--color-text-inverse)",
      "text-on-accent":   "var(--color-text-on-accent)",

      // Borders
      "border-subtle":    "var(--color-border-subtle)",
      "border-default":   "var(--color-border-default)",
      "border-strong":    "var(--color-border-strong)",
      "border-focus":     "var(--color-border-focus)",

      // Actions
      "action-primary":         "var(--color-action-primary)",
      "action-primary-hover":   "var(--color-action-primary-hover)",
      "action-primary-active":  "var(--color-action-primary-active)",
      "action-primary-text":    "var(--color-action-primary-text)",
      "action-secondary":       "var(--color-action-secondary)",
      "action-secondary-text":  "var(--color-action-secondary-text)",

      // Status — each status has bg/fg/border/solid (design-system.md §2.2)
      "success-bg":      "var(--color-success-bg)",
      "success-fg":      "var(--color-success-fg)",
      "success-border":  "var(--color-success-border)",
      "success-solid":   "var(--color-success-solid)",
      "warning-bg":      "var(--color-warning-bg)",
      "warning-fg":      "var(--color-warning-fg)",
      "warning-border":  "var(--color-warning-border)",
      "warning-solid":   "var(--color-warning-solid)",
      "danger-bg":       "var(--color-danger-bg)",
      "danger-fg":       "var(--color-danger-fg)",
      "danger-border":   "var(--color-danger-border)",
      "danger-solid":    "var(--color-danger-solid)",
      "info-bg":         "var(--color-info-bg)",
      "info-fg":         "var(--color-info-fg)",
      "info-border":     "var(--color-info-border)",
      "info-solid":      "var(--color-info-solid)",
    },

    // Spacing — only design-system steps compile. Off-grid utilities
    // (p-3.5, m-7, gap-9) fail at build time.
    spacing: {
      "0":     "var(--space-0)",
      "0.5":   "var(--space-0-5)",
      "1":     "var(--space-1)",
      "1.5":   "var(--space-1-5)",
      "2":     "var(--space-2)",
      "3":     "var(--space-3)",
      "4":     "var(--space-4)",
      "5":     "var(--space-5)",
      "6":     "var(--space-6)",
      "8":     "var(--space-8)",
      "10":    "var(--space-10)",
      "12":    "var(--space-12)",
      "16":    "var(--space-16)",
      "20":    "var(--space-20)",
      "24":    "var(--space-24)",
    },

    borderRadius: {
      "none":    "var(--radius-none)",
      "sm":      "var(--radius-sm)",
      "DEFAULT": "var(--radius-md)",
      "md":      "var(--radius-md)",
      "lg":      "var(--radius-lg)",
      "xl":      "var(--radius-xl)",
      "full":    "var(--radius-full)",
    },

    boxShadow: {
      "none":         "var(--shadow-0)",
      "sm":           "var(--shadow-1)",
      "DEFAULT":      "var(--shadow-2)",
      "md":           "var(--shadow-2)",
      "lg":           "var(--shadow-3)",
      "xl":           "var(--shadow-4)",
      "focus":        "var(--shadow-focus-ring)",
      "focus-danger": "var(--shadow-focus-ring-danger)",
    },

    fontFamily: {
      // `sans` is Tailwind's default for everything unqualified, so
      // pointing it at body keeps containers rendering correctly with
      // class="" and similar.
      sans:    "var(--font-body)",
      display: "var(--font-display)",
      body:    "var(--font-body)",
      mono:    "var(--font-mono)",
    },

    // Semantic font sizes — text-h1, text-body, text-caption, etc.
    // Each tuple is [size, { lineHeight, letterSpacing }]. Token
    // names are taken straight from design-system.md §3.2.
    fontSize: {
      "display-lg": ["var(--text-display-lg-size)", { lineHeight: "var(--text-display-line-height)", letterSpacing: "var(--text-display-tracking)" }],
      "display-md": ["var(--text-display-md-size)", { lineHeight: "var(--text-display-line-height)", letterSpacing: "var(--text-display-tracking)" }],
      "display-sm": ["var(--text-display-sm-size)", { lineHeight: "var(--text-display-line-height)", letterSpacing: "var(--text-display-tracking)" }],
      "h1":         ["var(--text-h1-size)",         { lineHeight: "var(--text-heading-line-height)", letterSpacing: "var(--text-heading-tracking)" }],
      "h2":         ["var(--text-h2-size)",         { lineHeight: "var(--text-heading-line-height)", letterSpacing: "var(--text-heading-tracking)" }],
      "h3":         ["var(--text-h3-size)",         { lineHeight: "var(--text-tight-line-height)",   letterSpacing: "var(--text-body-tracking)" }],
      "h4":         ["var(--text-h4-size)",         { lineHeight: "var(--text-tight-line-height)",   letterSpacing: "var(--text-body-tracking)" }],
      "body-lg":    ["var(--text-body-lg-size)",    { lineHeight: "var(--text-body-line-height)",    letterSpacing: "var(--text-body-tracking)" }],
      "body":       ["var(--text-body-size)",       { lineHeight: "var(--text-body-line-height)",    letterSpacing: "var(--text-body-tracking)" }],
      "body-sm":    ["var(--text-body-sm-size)",    { lineHeight: "var(--text-body-line-height)",    letterSpacing: "var(--text-body-tracking)" }],
      "caption":    ["var(--text-caption-size)",    { lineHeight: "var(--text-tight-line-height)",   letterSpacing: "var(--text-body-tracking)" }],
      "label":      ["var(--text-label-size)",      { lineHeight: "var(--text-tight-line-height)",   letterSpacing: "var(--text-label-tracking)" }],
      "code":       ["var(--text-code-size)",       { lineHeight: "var(--text-body-line-height)",    letterSpacing: "var(--text-body-tracking)" }],
    },

    fontWeight: {
      regular:  "var(--font-weight-regular)",
      medium:   "var(--font-weight-medium)",
      semibold: "var(--font-weight-semibold)",
      bold:     "var(--font-weight-bold)",
    },

    transitionDuration: {
      "instant": "var(--duration-instant)",
      "fast":    "var(--duration-fast)",
      "normal":  "var(--duration-normal)",
      "slow":    "var(--duration-slow)",
      "DEFAULT": "var(--duration-fast)",
    },

    transitionTimingFunction: {
      "linear":  "var(--ease-linear)",
      "out":     "var(--ease-out)",
      "in":      "var(--ease-in)",
      "in-out":  "var(--ease-in-out)",
      "spring":  "var(--ease-spring)",
      "DEFAULT": "var(--ease-out)",
    },

    zIndex: {
      "base":     "var(--z-base)",
      "raised":   "var(--z-raised)",
      "dropdown": "var(--z-dropdown)",
      "sticky":   "var(--z-sticky)",
      "overlay":  "var(--z-overlay)",
      "modal":    "var(--z-modal)",
      "popover":  "var(--z-popover)",
      "toast":    "var(--z-toast)",
      "debug":    "var(--z-debug)",
    },

    // Tailwind's default `screens` (sm/md/lg/xl/2xl) are kept. The
    // design system doesn't define breakpoint tokens; viewport-level
    // breakpoints are a portal concern.
    extend: {
      // Reserved for future additions. Empty in M3-01.
    },
  },

  plugins: [],
};
