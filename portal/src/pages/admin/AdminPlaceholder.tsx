interface AdminPlaceholderProps {
  title: string;
  comingIn: string; // e.g. "M4-19"
}

/**
 * Placeholder for admin pages whose real implementations land in
 * subsequent prompts. Mounted by App.tsx for each /admin/* route
 * during M4-17. Replaced by the real page component as each prompt
 * (M4-18 through M4-24) ships.
 *
 * After M4-24, no AdminPlaceholder invocations should remain in
 * App.tsx — M4-25 verifies via grep.
 */
export function AdminPlaceholder({ title, comingIn }: AdminPlaceholderProps) {
  return (
    <div className="px-6 py-8">
      <header className="max-w-6xl mx-auto mb-6">
        <h1 className="font-display text-h1 font-semibold text-text-primary">
          {title}
        </h1>
        <p className="text-body text-text-secondary mt-2">
          This view arrives in {comingIn}.
        </p>
      </header>
    </div>
  );
}
