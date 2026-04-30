import { useEffect, useRef } from "react";
import { X } from "lucide-react";

import {
  CopyableField,
  Field,
  Section,
} from "@/components/admin/Drawer";
import { formatRelativeTime } from "@/lib/time";
import type { AuditRead } from "@/types/admin";


interface AuditDetailDrawerProps {
  row: AuditRead | null;
  onClose: () => void;
}


/**
 * Audit row detail drawer. Purely presentational — takes the row by
 * prop, no fetch. Audit rows are immutable; the list cache already
 * has every field. (M4-22 / M4-23 drawers fetch on open because their
 * detail endpoints carry server-computed fields the list omits.)
 */
export function AuditDetailDrawer({
  row,
  onClose,
}: AuditDetailDrawerProps) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (row === null) return;
    closeRef.current?.focus();
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [row, onClose]);

  if (row === null) return null;

  const hasResource =
    row.resource_type !== null || row.resource_id !== null;
  const hasDetails =
    row.details !== null && Object.keys(row.details).length > 0;

  return (
    <>
      <div
        className="fixed inset-0 bg-surface-overlay z-overlay"
        onClick={onClose}
        aria-hidden
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby="audit-drawer-heading"
        className={
          "fixed top-0 right-0 bottom-0 w-full max-w-[28rem] z-modal " +
          "bg-surface-1 border-l border-border-default shadow-lg " +
          "flex flex-col"
        }
      >
        <header className="flex items-start justify-between p-5 border-b border-border-subtle">
          <div className="min-w-0">
            <h2
              id="audit-drawer-heading"
              className="font-body text-h3 font-semibold text-text-primary truncate"
            >
              Audit row
            </h2>
            <p className="text-caption text-text-tertiary mt-1 font-mono">
              #{row.id}
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close detail drawer"
            className="p-1 rounded-md hover:bg-surface-2 text-text-secondary"
          >
            <X size={18} aria-hidden />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto p-5 flex flex-col gap-4">
          <Section title="When">
            <Field label="Timestamp">
              <span
                className="text-text-secondary"
                title={row.timestamp}
              >
                {formatRelativeTime(row.timestamp)}
              </span>
            </Field>
            <Field label="ISO">
              <span className="font-mono text-caption text-text-tertiary">
                {row.timestamp}
              </span>
            </Field>
          </Section>

          <Section title="Action">
            <Field label="Actor">
              {row.actor !== null ? (
                <span className="font-mono text-text-primary">
                  {row.actor}
                </span>
              ) : (
                <span className="text-text-tertiary italic">system</span>
              )}
            </Field>
            <Field label="Action">
              <span className="font-mono text-text-primary">
                {row.action}
              </span>
            </Field>
            <Field label="Client IP">
              <span className="font-mono text-text-secondary">
                {row.client_ip ?? "—"}
              </span>
            </Field>
          </Section>

          {hasResource && (
            <Section title="Resource">
              <Field label="Type">
                <span className="text-text-secondary">
                  {row.resource_type ?? "—"}
                </span>
              </Field>
              {row.resource_id !== null ? (
                <CopyableField label="ID" value={row.resource_id} />
              ) : (
                <Field label="ID">
                  <span className="text-text-tertiary">—</span>
                </Field>
              )}
            </Section>
          )}

          {hasDetails && (
            <Section title="Details">
              <pre
                className={
                  "p-3 rounded-md bg-surface-2 border " +
                  "border-border-subtle text-caption text-text-secondary " +
                  "font-mono overflow-x-auto"
                }
              >
                {JSON.stringify(row.details, null, 2)}
              </pre>
            </Section>
          )}
        </div>
      </aside>
    </>
  );
}
