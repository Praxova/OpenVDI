import { BrandMark } from "@/components/BrandMark";

/**
 * Full-page placeholder shown while AuthContext is in the
 * `initializing` state — the brief window after app load while the
 * /auth/refresh round-trip resolves. Most users see it for ~50-200ms
 * if they have a valid refresh cookie; users without bounce to
 * /login the moment refresh fails.
 *
 * Visual: brand mark + small spinner + "Signing in…" copy. Honors
 * light/dark mode automatically via the theme bridge.
 */
export function InitializingScreen() {
  return (
    <div
      className={
        "fixed inset-0 flex flex-col items-center justify-center " +
        "bg-bg gap-6"
      }
      role="status"
      aria-label="Signing in"
    >
      <BrandMark size={48} />
      <div className="flex items-center gap-3 text-text-secondary">
        <div
          className={
            "w-5 h-5 border-2 border-action-primary border-t-transparent " +
            "rounded-full animate-spin"
          }
          aria-hidden
        />
        <p className="text-body">Signing in…</p>
      </div>
    </div>
  );
}
