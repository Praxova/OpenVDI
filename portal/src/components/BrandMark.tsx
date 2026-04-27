interface BrandMarkProps {
  /** Square pixel size. Default 32. */
  size?: number;
  className?: string;
}

/**
 * Praxova Vertex mark, theme-aware.
 *
 * Per design-system.md §2.4: the gold mark stays gold on dark
 * surfaces and dark on light surfaces. Both treatments are correct;
 * mixing them breaks the brand contract.
 *
 * Implementation: two `<img>` elements, one shown per theme via the
 * `dark:` variant (which we mapped to `[data-theme="dark"]` in
 * tailwind.config.js).
 *
 * Two `<img>` elements is uglier than one but it works without JS,
 * with prerender, with reduced-motion, and produces zero flicker on
 * theme toggle — the browser swaps which element is `display:none`
 * instantly.
 *
 * `alt=""` on the inner images is intentional: the outer `<span>`
 * carries the accessible label, so two redundant alts would have
 * screen readers announce the mark twice.
 */
export function BrandMark({ size = 32, className = "" }: BrandMarkProps) {
  return (
    <span
      className={`inline-block ${className}`}
      style={{ width: size, height: size }}
      aria-label="Praxova"
      role="img"
    >
      <img
        src="/brand/vertex-dark.svg"
        alt=""
        width={size}
        height={size}
        className="block dark:hidden"
      />
      <img
        src="/brand/vertex-gold.svg"
        alt=""
        width={size}
        height={size}
        className="hidden dark:block"
      />
    </span>
  );
}
