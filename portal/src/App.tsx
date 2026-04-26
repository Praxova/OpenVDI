// M3-01 placeholder. M3-03 replaces this with the AppShell + real routing.
//
// The composition deliberately exercises the full token-bridge chain:
// surface tokens (bg-bg, bg-surface-1), border (border-border-subtle),
// radius (rounded-lg), shadow (shadow-md), spacing (p-6, mt-2, mt-4),
// text colors (primary/secondary/tertiary), display vs body fonts,
// and the type scale (text-h1, text-body, text-caption). If any of
// these don't resolve, the placeholder breaks visibly — by design.
export default function App() {
  return (
    <main className="min-h-screen bg-bg flex items-center justify-center p-6">
      <section
        aria-labelledby="placeholder-title"
        className="bg-surface-1 border border-border-subtle rounded-lg shadow-md p-6 max-w-md"
      >
        <h1
          id="placeholder-title"
          className="font-display text-h1 font-semibold text-text-primary"
        >
          OpenVDI Portal
        </h1>
        <p className="text-body text-text-secondary mt-2">
          Scaffold up. Design system wired. Next stop: API client and dev-auth login.
        </p>
        <p className="text-caption text-text-tertiary mt-4">
          M3-01 complete · Praxova design system v0.1
        </p>
      </section>
    </main>
  );
}
