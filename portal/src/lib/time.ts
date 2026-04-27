/**
 * Format an ISO 8601 timestamp as a friendly relative-or-absolute
 * string in the user's locale.
 *
 *   <1 minute ago     → "just now"
 *   <60 minutes ago   → "N minutes ago"
 *   <24 hours ago     → "N hours ago"
 *   within last 7d    → "Mon, 3:42 PM" (weekday + time)
 *   older             → "Mar 14, 2026" (locale-formatted date)
 *
 * Lifted from M3-04's PoolCard. Now consumed by both PoolCard
 * (assigned-desktop summary's "Last connected ...") and SessionRow
 * (the Connected column).
 *
 * If a future consumer wants different semantics — durations
 * ("5m 23s"), absolute-only formatting, or a more granular bucket
 * scheme — introduce a sibling helper rather than overloading this
 * one. The current shape is "user-facing rough relative time."
 */
export function formatRelativeTime(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const now = Date.now();
  const diffMs = now - t;
  const diffMin = Math.round(diffMs / 60_000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin} minute${diffMin === 1 ? "" : "s"} ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr} hour${diffHr === 1 ? "" : "s"} ago`;
  const date = new Date(t);
  const diffDays = Math.floor(diffMs / 86_400_000);
  if (diffDays < 7) {
    return date.toLocaleString(undefined, {
      weekday: "short",
      hour: "numeric",
      minute: "2-digit",
    });
  }
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}
