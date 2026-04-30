import { useEffect, useRef } from "react";
import { X } from "lucide-react";

import { useSessionDetailQuery } from "@/api/admin/sessions";
import {
  CopyableField,
  Field,
  Section,
} from "@/components/admin/Drawer";
import {
  StatusBadge,
  sessionStatusBadge,
} from "@/components/StatusBadge";
import { formatRelativeTime } from "@/lib/time";


interface SessionDetailDrawerProps {
  sessionId: string | null;
  onClose: () => void;
}


export function SessionDetailDrawer({
  sessionId,
  onClose,
}: SessionDetailDrawerProps) {
  const detail = useSessionDetailQuery(sessionId);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (sessionId === null) return;
    closeRef.current?.focus();
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [sessionId, onClose]);

  if (sessionId === null) return null;

  const hasGuestTelemetry =
    detail.data !== undefined &&
    (detail.data.os_user !== null ||
      detail.data.os_info !== null ||
      detail.data.vm_ip_address !== null ||
      detail.data.idle_since !== null);

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
        aria-labelledby="session-drawer-heading"
        className={
          "fixed top-0 right-0 bottom-0 w-full max-w-[28rem] z-modal " +
          "bg-surface-1 border-l border-border-default shadow-lg " +
          "flex flex-col"
        }
      >
        <header className="flex items-start justify-between p-5 border-b border-border-subtle">
          <div className="min-w-0">
            <h2
              id="session-drawer-heading"
              className="font-body text-h3 font-semibold text-text-primary truncate"
            >
              Session
            </h2>
            {detail.data !== undefined && (
              <p className="text-caption text-text-tertiary mt-1 font-mono">
                {detail.data.username}
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
              Couldn't load session details.
            </p>
          )}
          {detail.data !== undefined && (
            <>
              <Section title="Identity">
                <CopyableField
                  label="Session ID"
                  value={detail.data.id}
                />
                {detail.data.desktop_id !== null ? (
                  <CopyableField
                    label="Desktop ID"
                    value={detail.data.desktop_id}
                  />
                ) : (
                  <Field label="Desktop">
                    <span className="text-text-tertiary italic">
                      (deleted)
                    </span>
                  </Field>
                )}
                {detail.data.pool_id !== null ? (
                  <CopyableField
                    label="Pool ID"
                    value={detail.data.pool_id}
                  />
                ) : (
                  <Field label="Pool">
                    <span className="text-text-tertiary italic">
                      (deleted)
                    </span>
                  </Field>
                )}
              </Section>

              <Section title="Status">
                <Field label="Lifecycle">
                  <StatusBadge
                    {...sessionStatusBadge(detail.data.status)}
                  />
                </Field>
                <Field label="Protocol">
                  <span className="font-mono text-text-secondary">
                    {detail.data.protocol}
                  </span>
                </Field>
                <Field label="Client IP">
                  <span className="font-mono text-text-secondary">
                    {detail.data.client_ip ?? "—"}
                  </span>
                </Field>
              </Section>

              <Section title="Times">
                <Field label="Created">
                  <span className="text-text-secondary">
                    {formatRelativeTime(detail.data.created_at)}
                  </span>
                </Field>
                <Field label="Connected">
                  <span className="text-text-secondary">
                    {detail.data.connected_at !== null
                      ? formatRelativeTime(detail.data.connected_at)
                      : "—"}
                  </span>
                </Field>
                <Field label="Disconnected">
                  <span className="text-text-secondary">
                    {detail.data.disconnected_at !== null
                      ? formatRelativeTime(detail.data.disconnected_at)
                      : "—"}
                  </span>
                </Field>
                <Field label="Ended">
                  <span className="text-text-secondary">
                    {detail.data.ended_at !== null
                      ? formatRelativeTime(detail.data.ended_at)
                      : "—"}
                  </span>
                </Field>
                <Field label="Last heartbeat">
                  <span className="text-text-secondary">
                    {detail.data.last_heartbeat !== null
                      ? formatRelativeTime(detail.data.last_heartbeat)
                      : "—"}
                  </span>
                </Field>
              </Section>

              {hasGuestTelemetry && (
                <Section title="Guest telemetry">
                  <Field label="OS user">
                    <span className="font-mono text-text-primary">
                      {detail.data.os_user ?? "—"}
                    </span>
                  </Field>
                  <Field label="VM IP">
                    <span className="font-mono text-text-secondary">
                      {detail.data.vm_ip_address ?? "—"}
                    </span>
                  </Field>
                  <Field label="Idle since">
                    <span className="text-text-secondary">
                      {detail.data.idle_since !== null
                        ? formatRelativeTime(detail.data.idle_since)
                        : "—"}
                    </span>
                  </Field>
                  {detail.data.os_info !== null && (
                    <div className="mt-2">
                      <dt className="text-text-tertiary text-body-sm mb-1">
                        OS info
                      </dt>
                      <dd>
                        <pre
                          className={
                            "p-3 rounded-md bg-surface-2 border " +
                            "border-border-subtle text-caption " +
                            "text-text-secondary font-mono overflow-x-auto"
                          }
                        >
                          {JSON.stringify(detail.data.os_info, null, 2)}
                        </pre>
                      </dd>
                    </div>
                  )}
                </Section>
              )}
            </>
          )}
        </div>
      </aside>
    </>
  );
}


