import {
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Check, Copy, X } from "lucide-react";

import { useDesktopDetailQuery } from "@/api/admin/desktops";
import {
  StatusBadge,
  desktopStatusBadge,
  sessionStatusBadge,
} from "@/components/StatusBadge";
import { formatRelativeTime } from "@/lib/time";


interface DesktopDetailDrawerProps {
  desktopId: string | null;
  onClose: () => void;
}


export function DesktopDetailDrawer({
  desktopId,
  onClose,
}: DesktopDetailDrawerProps) {
  const detail = useDesktopDetailQuery(desktopId);
  const closeRef = useRef<HTMLButtonElement>(null);

  // Move focus to close button on open; ESC closes.
  useEffect(() => {
    if (desktopId === null) return;
    closeRef.current?.focus();
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [desktopId, onClose]);

  if (desktopId === null) return null;

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
        aria-labelledby="drawer-heading"
        className={
          "fixed top-0 right-0 bottom-0 w-full max-w-[28rem] z-modal " +
          "bg-surface-1 border-l border-border-default shadow-lg " +
          "flex flex-col"
        }
      >
        <header className="flex items-start justify-between p-5 border-b border-border-subtle">
          <div className="min-w-0">
            <h2
              id="drawer-heading"
              className="font-body text-h3 font-semibold text-text-primary truncate"
            >
              {detail.data?.name ?? "Desktop"}
            </h2>
            {detail.data !== undefined && (
              <p className="text-caption text-text-tertiary mt-1">
                {detail.data.pve_node} · vmid {detail.data.pve_vmid}
              </p>
            )}
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
          {detail.isPending && (
            <p className="text-text-tertiary">Loading…</p>
          )}
          {detail.error !== null && (
            <p role="alert" className="text-danger-fg">
              Couldn't load desktop details.
            </p>
          )}
          {detail.data !== undefined && (
            <>
              <Section title="Identity">
                <CopyableField
                  label="Desktop ID"
                  value={detail.data.id}
                />
                <CopyableField
                  label="Pool ID"
                  value={detail.data.pool_id}
                />
              </Section>

              <Section title="Status">
                <Field label="Lifecycle">
                  <StatusBadge {...desktopStatusBadge(detail.data.status)} />
                </Field>
                <Field label="Power state">
                  <span className="font-mono text-text-secondary">
                    {detail.data.live_power_state}
                  </span>
                </Field>
                {detail.data.error_message !== null && (
                  <Field label="Error">
                    <span className="text-danger-fg text-body-sm">
                      {detail.data.error_message}
                    </span>
                  </Field>
                )}
              </Section>

              <Section title="Assignment">
                <Field label="Assigned user">
                  {detail.data.assigned_user !== null ? (
                    <span className="font-mono text-text-primary">
                      {detail.data.assigned_user}
                    </span>
                  ) : (
                    <span className="text-text-tertiary">—</span>
                  )}
                </Field>
                {detail.data.assignment_type !== null && (
                  <Field label="Type">
                    <span className="text-text-secondary capitalize">
                      {detail.data.assignment_type}
                    </span>
                  </Field>
                )}
              </Section>

              {detail.data.active_session !== null && (
                <Section title="Active session">
                  <CopyableField
                    label="Session ID"
                    value={detail.data.active_session.id}
                  />
                  <Field label="Status">
                    <StatusBadge
                      {...sessionStatusBadge(
                        detail.data.active_session.status,
                      )}
                    />
                  </Field>
                  <Field label="OS user">
                    <span className="font-mono text-text-primary">
                      {detail.data.active_session.os_user ?? "—"}
                    </span>
                  </Field>
                  <Field label="Connected at">
                    <span className="text-text-secondary">
                      {formatRelativeTime(
                        detail.data.active_session.created_at,
                      )}
                    </span>
                  </Field>
                </Section>
              )}

              <Section title="History">
                <Field label="Provisioned">
                  <span className="text-text-secondary">
                    {detail.data.provisioned_at !== null
                      ? formatRelativeTime(detail.data.provisioned_at)
                      : "Never"}
                  </span>
                </Field>
                <Field label="Last connected">
                  <span className="text-text-secondary">
                    {detail.data.last_connected !== null
                      ? formatRelativeTime(detail.data.last_connected)
                      : "Never"}
                  </span>
                </Field>
                <Field label="Last disconnected">
                  <span className="text-text-secondary">
                    {detail.data.last_disconnected !== null
                      ? formatRelativeTime(detail.data.last_disconnected)
                      : "Never"}
                  </span>
                </Field>
              </Section>
            </>
          )}
        </div>
      </aside>
    </>
  );
}


interface SectionProps {
  title: string;
  children: ReactNode;
}


function Section({ title, children }: SectionProps) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="font-body text-body-sm font-semibold text-text-tertiary uppercase tracking-wide">
        {title}
      </h3>
      <dl className="flex flex-col gap-1.5">{children}</dl>
    </section>
  );
}


interface FieldProps {
  label: string;
  children: ReactNode;
}


function Field({ label, children }: FieldProps) {
  return (
    <div className="grid grid-cols-[8rem_1fr] gap-2 items-baseline">
      <dt className="text-text-tertiary text-body-sm">{label}</dt>
      <dd className="text-body-sm">{children}</dd>
    </div>
  );
}


interface CopyableFieldProps {
  label: string;
  value: string;
}


function CopyableField({ label, value }: CopyableFieldProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Best effort — older browsers / non-secure contexts may fail.
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
