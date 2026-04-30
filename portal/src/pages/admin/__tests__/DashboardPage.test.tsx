import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { DashboardPage } from "@/pages/admin";
import type {
  AuditEntry,
  ClusterRead,
  DashboardSummary,
} from "@/types/admin";

// Hoisted mock state. Tests mutate `clientMock.fixtures` per case;
// the mocked useBrokerClient.get dispatches on path.
const clientMock = vi.hoisted(() => ({
  fixtures: {
    summary: null as DashboardSummary | Error | null,
    clusters: null as ClusterRead[] | Error | null,
    audit: null as AuditEntry[] | Error | null,
  },
}));

vi.mock("@/api/client", () => {
  return {
    useBrokerClient: () => ({
      get: vi.fn(async (path: string) => {
        if (path.includes("/dashboard/summary")) {
          const v = clientMock.fixtures.summary;
          if (v instanceof Error) throw v;
          return v;
        }
        if (path.startsWith("/api/v1/clusters")) {
          const v = clientMock.fixtures.clusters;
          if (v instanceof Error) throw v;
          return v ?? [];
        }
        if (path.startsWith("/api/v1/audit")) {
          const v = clientMock.fixtures.audit;
          if (v instanceof Error) throw v;
          return v ?? [];
        }
        throw new Error(`unexpected path: ${path}`);
      }),
    }),
  };
});

function emptySummary(): DashboardSummary {
  return {
    clusters: { total: 0, by_status: {} },
    pools: {
      total: 0,
      by_status: {} as DashboardSummary["pools"]["by_status"],
      by_type: {} as DashboardSummary["pools"]["by_type"],
    },
    desktops: { total: 0, by_status: {} },
    sessions: {
      total: 0, active: 0, connecting: 0, disconnected: 0, ended: 0,
    },
    capacity: { total_vmid_slots: 0, total_desktops: 0 },
  };
}

function makeCluster(name: string, status: ClusterRead["status"]): ClusterRead {
  return {
    id: name,
    name,
    provider_type: "proxmox",
    api_url: "https://test:8006",
    token_id: "x@pve!y",
    verify_ssl: true,
    node_filter: null,
    provider_config: {},
    status,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <DashboardPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("DashboardPage", () => {
  beforeEach(() => {
    clientMock.fixtures = {
      summary: emptySummary(),
      clusters: [],
      audit: [],
    };
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders heading + four cards", async () => {
    renderPage();
    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: /admin dashboard/i,
      }),
    ).toBeDefined();
    expect(screen.getByText("Capacity")).toBeDefined();
    expect(screen.getByText("Sessions")).toBeDefined();
    expect(screen.getByText("Cluster Health")).toBeDefined();
    expect(screen.getByText("Recent Activity")).toBeDefined();
  });

  it("populates capacity card from summary", async () => {
    clientMock.fixtures.summary = {
      ...emptySummary(),
      pools: {
        total: 3,
        by_status: {} as DashboardSummary["pools"]["by_status"],
        by_type: {} as DashboardSummary["pools"]["by_type"],
      },
      desktops: {
        total: 12,
        by_status: { available: 5, assigned: 4, connected: 2, error: 1 },
      },
      capacity: { total_vmid_slots: 200, total_desktops: 12 },
    };
    renderPage();
    expect(await screen.findByText("12")).toBeDefined(); // Desktops total
    expect(screen.getByText("5")).toBeDefined();         // Available
    expect(screen.getByText("6")).toBeDefined();         // Assigned (4+2)
    expect(screen.getByText("200")).toBeDefined();       // VMID slots
  });

  it("renders sessions counts", async () => {
    clientMock.fixtures.summary = {
      ...emptySummary(),
      sessions: {
        total: 7, active: 3, connecting: 1, disconnected: 1, ended: 2,
      },
    };
    renderPage();
    expect(await screen.findByText("3")).toBeDefined(); // Active
    // 1 appears twice (connecting + disconnected); both render.
    expect(screen.getAllByText("1").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("2")).toBeDefined();
  });

  it("lists clusters with status badges", async () => {
    clientMock.fixtures.clusters = [
      makeCluster("pve1", "active"),
      makeCluster("pve2", "offline"),
    ];
    renderPage();
    expect(await screen.findByText("pve1")).toBeDefined();
    expect(screen.getByText("pve2")).toBeDefined();
    expect(screen.getByText("active")).toBeDefined();
    expect(screen.getByText("offline")).toBeDefined();
  });

  it("renders empty state for clusters when none registered", async () => {
    clientMock.fixtures.clusters = [];
    renderPage();
    expect(
      await screen.findByText(/No clusters registered yet/i),
    ).toBeDefined();
  });

  it("renders error state when summary query fails", async () => {
    clientMock.fixtures.summary = new Error("500");
    renderPage();
    // Both Capacity + Sessions consume summary; both flip to error.
    const errors = await screen.findAllByText(/Couldn't load this card/i);
    expect(errors.length).toBe(2);
  });

  it("lists recent audit entries with relative time + actor", async () => {
    clientMock.fixtures.audit = [
      {
        id: 1,
        timestamp: new Date().toISOString(),
        actor: "alice",
        action: "pool.create",
        resource_type: "pool",
        resource_id: "abc",
        details: null,
        client_ip: null,
      },
    ];
    renderPage();
    expect(await screen.findByText("pool.create")).toBeDefined();
    expect(screen.getByText(/by alice/)).toBeDefined();
  });

  it("renders empty state for audit when no events", async () => {
    clientMock.fixtures.audit = [];
    renderPage();
    expect(
      await screen.findByText(/No audit events yet/i),
    ).toBeDefined();
  });
});
