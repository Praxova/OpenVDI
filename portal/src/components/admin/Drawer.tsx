import { useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";


export interface SectionProps {
  title: string;
  children: ReactNode;
}


/**
 * Section header + body wrapper used inside detail drawers
 * (DesktopDetailDrawer, SessionDetailDrawer, AuditDetailDrawer).
 *
 * Renders the title in tertiary uppercase + a `<dl>` for the body.
 * Children are typically <Field> or <CopyableField> rows.
 */
export function Section({ title, children }: SectionProps) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="font-body text-body-sm font-semibold text-text-tertiary uppercase tracking-wide">
        {title}
      </h3>
      <dl className="flex flex-col gap-1.5">{children}</dl>
    </section>
  );
}


export interface FieldProps {
  label: string;
  children: ReactNode;
}


/**
 * Two-column label/value row inside a <Section>. Uses a CSS grid with
 * a fixed 8rem label column so labels align across rows in the same
 * section.
 */
export function Field({ label, children }: FieldProps) {
  return (
    <div className="grid grid-cols-[8rem_1fr] gap-2 items-baseline">
      <dt className="text-text-tertiary text-body-sm">{label}</dt>
      <dd className="text-body-sm">{children}</dd>
    </div>
  );
}


export interface CopyableFieldProps {
  label: string;
  value: string;
}


/**
 * Field whose value is a long string the admin will want to copy
 * (UUIDs, principal names, etc.). Renders the value in monospace +
 * a copy button that briefly flashes a check icon on success.
 *
 * Uses `navigator.clipboard.writeText`. Browsers in non-secure
 * contexts reject the call; in production the same-origin TLS
 * requirement (deploy.md) makes that a non-issue. The catch swallows
 * silently — copy is a convenience, not a load-bearing flow.
 */
export function CopyableField({ label, value }: CopyableFieldProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Best effort.
    }
  };

  return (
    <div className="grid grid-cols-[8rem_1fr] gap-2 items-baseline">
      <dt className="text-text-tertiary text-body-sm">{label}</dt>
      <dd className="text-body-sm flex items-center gap-2 min-w-0">
        <span className="font-mono text-text-primary truncate">
          {value}
        </span>
        <button
          type="button"
          onClick={handleCopy}
          aria-label={`Copy ${label}`}
          className="p-1 rounded hover:bg-surface-2 text-text-secondary flex-shrink-0"
        >
          {copied ? (
            <Check size={14} aria-hidden />
          ) : (
            <Copy size={14} aria-hidden />
          )}
        </button>
      </dd>
    </div>
  );
}
