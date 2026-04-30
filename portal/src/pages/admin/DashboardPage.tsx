import type { ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  Cpu,
  ScrollText,
  Server,
} from "lucide-react";

import {
  useClustersQuery,
  useDashboardSummaryQuery,
  useRecentAuditQuery,
} from "@/api/admin/dashboard";
import { StatusBadge, type StatusTone } from "@/components/StatusBadge";
import { formatRelativeTime } from "@/lib/time";
import type {
  AuditEntry,
  ClusterRead,
  ClusterStatus,
  DashboardSummary,
} from "@/types/admin";

/**
 * Read-only admin landing page. Per FE9: snapshot view, no mutations,
 * no live updates. Four cards each have their own query + state
 * machine; failure on one card doesn't block the others.
 *
 * Two cards (Capacity + Sessions) share useDashboardSummaryQuery —
 * TanStack Query de-duplicates network calls automatically.
 */
export function DashboardPage() {
  return (
    <div className="px-6 py-8">
      <header className="max-w-6xl mx-auto mb-6">
        <h1 className="font-display text-h1 font-semibold text-text-primary">
          Admin Dashboard
        </h1>
        <p className="text-body text-text-secondary mt-2">
          A read-only snapshot of the OpenVDI deployment.
        </p>
      </header>

      <div className="max-w-6xl mx-auto grid grid-cols-1 md:grid-cols-2 gap-6">
        <CapacityCard />
        <SessionsCard />
        <ClusterHealthCard />
        <RecentAuditCard />
      </div>
    </div>
  );
}


// ── Capacity card ───────────────────────────────────────────


function CapacityCard() {
  const { data, error, isPending } = useDashboardSummaryQuery();

  return (
    <Card title="Capacity" icon={<Cpu size={18} aria-hidden />}>
      {isPending ? (
        <CardSkeleton rows={3} />
      ) : error !== null ? (
        <CardError />
      ) : (
        <CapacityBody summary={data} />
      )}
    </Card>
  );
}


function CapacityBody({ summary }: { summary: DashboardSummary }) {
  const errorCount = summary.desktops.by_status["error"] ?? 0;
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-3 mt-3">
      <Stat label="Pools" value={summary.pools.total} />
      <Stat label="Desktops" value={summary.desktops.total} />
      <Stat label="VMID slots" value={summary.capacity.total_vmid_slots} />
      <Stat
        label="Available"
        value={summary.desktops.by_status["available"] ?? 0}
      />
      <Stat
        label="Assigned"
        value={
          (summary.desktops.by_status["assigned"] ?? 0)
          + (summary.desktops.by_status["connected"] ?? 0)
        }
      />
      <Stat
        label="In error"
        value={errorCount}
        tone={errorCount > 0 ? "danger" : "neutral"}
      />
    </dl>
  );
}


// ── Sessions card ───────────────────────────────────────────


function SessionsCard() {
  const { data, error, isPending } = useDashboardSummaryQuery();

  return (
    <Card title="Sessions" icon={<Activity size={18} aria-hidden />}>
      {isPending ? (
        <CardSkeleton rows={2} />
      ) : error !== null ? (
        <CardError />
      ) : (
        <SessionsBody sessions={data.sessions} />
      )}
    </Card>
  );
}


function SessionsBody({
  sessions,
}: {
  sessions: DashboardSummary["sessions"];
}) {
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-3 mt-3">
      <Stat
        label="Active"
        value={sessions.active}
        tone={sessions.active > 0 ? "success" : "neutral"}
      />
      <Stat label="Connecting" value={sessions.connecting} tone="info" />
      <Stat label="Disconnected" value={sessions.disconnected} />
      <Stat label="Ended (lifetime)" value={sessions.ended} />
    </dl>
  );
}


// ── Cluster health card ─────────────────────────────────────


function ClusterHealthCard() {
  const { data, error, isPending } = useClustersQuery();

  return (
    <Card title="Cluster Health" icon={<Server size={18} aria-hidden />}>
      {isPending ? (
        <CardSkeleton rows={3} />
      ) : error !== null ? (
        <CardError />
      ) : data.length === 0 ? (
        <p className="text-text-tertiary text-body-sm mt-3">
          No clusters registered yet.
        </p>
      ) : (
        <ul className="mt-3 divide-y divide-border-subtle">
          {data.map((c) => (
            <ClusterRow key={c.id} cluster={c} />
          ))}
        </ul>
      )}
    </Card>
  );
}


function ClusterRow({ cluster }: { cluster: ClusterRead }) {
  return (
    <li className="flex items-center justify-between py-2">
      <span className="text-body-sm text-text-primary truncate">
        {cluster.name}
      </span>
      <StatusBadge tone={clusterStatusTone(cluster.status)} label={cluster.status} />
    </li>
  );
}


function clusterStatusTone(status: ClusterStatus): StatusTone {
  switch (status) {
    case "active":      return "success";
    case "pending":     return "neutral";
    case "maintenance": return "warning";
    case "offline":     return "danger";
  }
}


// ── Recent audit card ───────────────────────────────────────


function RecentAuditCard() {
  const { data, error, isPending } = useRecentAuditQuery(10);

  return (
    <Card title="Recent Activity" icon={<ScrollText size={18} aria-hidden />}>
      {isPending ? (
        <CardSkeleton rows={4} />
      ) : error !== null ? (
        <CardError />
      ) : data.length === 0 ? (
        <p className="text-text-tertiary text-body-sm mt-3">
          No audit events yet.
        </p>
      ) : (
        <ul className="mt-3 divide-y divide-border-subtle">
          {data.map((e) => (
            <AuditRow key={e.id} entry={e} />
          ))}
        </ul>
      )}
    </Card>
  );
}


function AuditRow({ entry }: { entry: AuditEntry }) {
  return (
    <li className="py-2 text-body-sm">
      <div className="flex items-center justify-between gap-3">
        <span className="font-medium text-text-primary truncate">
          {entry.action}
        </span>
        <span className="text-text-tertiary text-caption whitespace-nowrap">
          {formatRelativeTime(entry.timestamp)}
        </span>
      </div>
      {entry.actor !== null && (
        <p className="text-text-secondary text-caption mt-0.5 truncate">
          by {entry.actor}
          {entry.resource_type !== null
            ? ` on ${entry.resource_type}`
            : ""}
        </p>
      )}
    </li>
  );
}


// ── Card primitives (inlined; not extracted) ────────────────


interface CardProps {
  title: string;
  icon: ReactNode;
  children: ReactNode;
}


function Card({ title, icon, children }: CardProps) {
  const titleId = `card-title-${title.replace(/\s+/g, "-").toLowerCase()}`;
  return (
    <section
      aria-labelledby={titleId}
      className={
        "bg-surface-1 border border-border-subtle rounded-lg shadow-sm " +
        "p-5"
      }
    >
      <header className="flex items-center gap-2">
        <span className="text-text-secondary">{icon}</span>
        <h2
          id={titleId}
          className="font-body text-h3 font-semibold text-text-primary"
        >
          {title}
        </h2>
      </header>
      {children}
    </section>
  );
}


function CardSkeleton({ rows }: { rows: number }) {
  return (
    <div
      className="space-y-3 mt-3"
      role="status"
      aria-label="Loading"
    >
      {Array.from({ length: rows }, (_, i) => (
        <div
          key={i}
          className="h-5 rounded-sm bg-surface-2 animate-pulse"
        />
      ))}
    </div>
  );
}


function CardError() {
  return (
    <div
      className="mt-3 flex items-center gap-2 text-danger-fg"
      role="alert"
    >
      <AlertTriangle size={16} strokeWidth={2} aria-hidden />
      <span className="text-body-sm">Couldn't load this card.</span>
    </div>
  );
}


interface StatProps {
  label: string;
  value: number;
  tone?: "info" | "success" | "warning" | "danger" | "neutral";
}


function Stat({ label, value, tone = "neutral" }: StatProps) {
  const valueClass =
    tone === "success" ? "text-success-fg"
    : tone === "info"  ? "text-info-fg"
    : tone === "warning" ? "text-warning-fg"
    : tone === "danger"  ? "text-danger-fg"
    : "text-text-primary";
  return (
    <div>
      <dd className={`font-display text-h2 font-semibold ${valueClass}`}>
        {value}
      </dd>
      <dt className="text-text-tertiary text-caption uppercase tracking-wide mt-1">
        {label}
      </dt>
    </div>
  );
}
