import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { PoolsPage } from "@/pages/admin";
import { BrokerError } from "@/api/errors";
import type {
  ClusterRead,
  PoolCapacityRow,
  PoolRead,
  PoolStatus,
  PoolType,
} from "@/types/admin";


const clientMock = vi.hoisted(() => ({
  getImpl: vi.fn(),
  postImpl: vi.fn(),
  deleteImpl: vi.fn(),
}));

vi.mock("@/api/client", () => ({
  useBrokerClient: () => ({
    get: clientMock.getImpl,
    post: clientMock.postImpl,
    delete: clientMock.deleteImpl,
    put: vi.fn(),
  }),
}));


function makePool(
  id: string,
  display: string,
  opts: Partial<PoolRead> = {},
): PoolRead {
  return {
    id,
    name: id,
    display_name: display,
    description: null,
    pool_type: "nonpersistent" as PoolType,
    template_id: "tpl-1",
    cluster_id: "c1",
    min_spare: 2,
    max_size: 10,
    vmid_range_start: 5000,
    vmid_range_end: 5099,
    name_prefix: "TEST",
    target_nodes: null,
    target_storage: null,
    cpu_cores: null,
    memory_mb: null,
    pve_pool_id: null,
    provider_config: {},
    auto_logoff_min: 0,
    delete_on_logoff: false,
    refresh_on_logoff: true,
    status: "active" as PoolStatus,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...opts,
  };
}

function makeCluster(id: string, name: string): ClusterRead {
  return {
    id,
    name,
    provider_type: "proxmox",
    api_url: "https://x:8006",
    token_id: "x@pve!y",
    verify_ssl: true,
    node_filter: null,
    provider_config: {},
    status: "active",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

function makeCapacity(poolId: string, available: number): PoolCapacityRow {
  return {
    pool_id: poolId,
    pool_name: poolId,
    pool_display_name: poolId,
    pool_status: "active" as PoolStatus,
    pool_type: "nonpersistent" as PoolType,
    range_capacity: 100,
    total_desktops: 10,
    free_slots: 90,
    provisioning: 0,
    available,
    assigned: 0,
    connected: 0,
    disconnected: 0,
    error: 0,
    deleting: 0,
    maintenance: 0,
  };
}

function setupGet(opts: {
  pools?: PoolRead[] | Error;
  clusters?: ClusterRead[] | Error;
  capacity?: PoolCapacityRow[] | Error;
}) {
  clientMock.getImpl.mockImplementation(async (path: string) => {
    if (path === "/api/v1/pools") {
      const v = opts.pools;
      if (v instanceof Error) throw v;
      return v ?? [];
    }
    if (path.startsWith("/api/v1/clusters")) {
      const v = opts.clusters;
      if (v instanceof Error) throw v;
      return v ?? [];
    }
    if (path.includes("/dashboard/capacity")) {
      const v = opts.capacity;
      if (v instanceof Error) throw v;
      return v ?? [];
    }
    throw new Error(`unexpected path: ${path}`);
  });
}

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <MemoryRouter initialEntries={["/admin/pools"]}>
      <QueryClientProvider client={qc}>
        <PoolsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("PoolsPage", () => {
  beforeEach(() => {
    Object.values(clientMock).forEach((m) => m.mockReset());
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders pool list with cluster names + capacity resolved", async () => {
    setupGet({
      pools: [makePool("eng", "Engineering")],
      clusters: [makeCluster("c1", "pve-prod")],
      capacity: [makeCapacity("eng", 5)],
    });
    renderPage();
    expect(await screen.findByText("Engineering")).toBeDefined();
    expect(screen.getByText("pve-prod")).toBeDefined();
    expect(screen.getByText("5/10")).toBeDefined();
  });

  it("renders empty state when no pools", async () => {
    setupGet({ pools: [], clusters: [], capacity: [] });
    renderPage();
    expect(
      await screen.findByText(/No pools yet/i),
    ).toBeDefined();
  });

  it("renders error state on pool query failure", async () => {
    setupGet({ pools: new Error("boom"), clusters: [], capacity: [] });
    renderPage();
    expect(
      await screen.findByText(/Couldn't load the list/i),
    ).toBeDefined();
  });

  it("delete confirms via window.confirm and invokes mutation", async () => {
    setupGet({
      pools: [makePool("eng", "Engineering")],
      clusters: [makeCluster("c1", "pve-prod")],
    });
    clientMock.deleteImpl.mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();

    renderPage();
    await screen.findByText("Engineering");
    await user.click(
      screen.getByRole("button", { name: /delete engineering/i }),
    );
    await waitFor(() => {
      expect(clientMock.deleteImpl).toHaveBeenCalledWith(
        "/api/v1/pools/eng",
      );
    });
  });

  it("delete shows 409 inline error (active sessions)", async () => {
    setupGet({
      pools: [makePool("eng", "Engineering")],
      clusters: [makeCluster("c1", "pve-prod")],
    });
    clientMock.deleteImpl.mockRejectedValue(
      new BrokerError({
        httpStatus: 409,
        code: "CONFLICT",
        message: "active sessions",
        envelope: null,
      }),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();

    renderPage();
    await screen.findByText("Engineering");
    await user.click(
      screen.getByRole("button", { name: /delete engineering/i }),
    );
    expect(
      await screen.findByText(/Active sessions exist/i),
    ).toBeDefined();
  });

  it("provision button only on non-persistent + active pools", async () => {
    setupGet({
      pools: [
        makePool("eng", "Engineering", { pool_type: "nonpersistent", status: "active" }),
        makePool("kiosk", "Kiosk", { pool_type: "persistent", status: "active" }),
        makePool("dis", "Disabled", { pool_type: "nonpersistent", status: "disabled" }),
      ],
      clusters: [makeCluster("c1", "pve-prod")],
    });
    renderPage();
    await screen.findByText("Engineering");

    expect(
      screen.queryByRole("button", { name: /provision warm spares for engineering/i }),
    ).not.toBeNull();
    expect(
      screen.queryByRole("button", { name: /provision warm spares for kiosk/i }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: /provision warm spares for disabled/i }),
    ).toBeNull();
  });

  it("drain button only on active pools", async () => {
    setupGet({
      pools: [
        makePool("eng", "Engineering", { status: "active" }),
        makePool("dr", "Draining", { status: "draining" }),
      ],
      clusters: [makeCluster("c1", "pve-prod")],
    });
    renderPage();
    await screen.findByText("Engineering");

    expect(
      screen.queryByRole("button", { name: /drain engineering/i }),
    ).not.toBeNull();
    expect(
      screen.queryByRole("button", { name: /drain draining/i }),
    ).toBeNull();
  });

  it("provision shows success banner on 202", async () => {
    setupGet({
      pools: [makePool("eng", "Engineering")],
      clusters: [makeCluster("c1", "pve-prod")],
    });
    clientMock.postImpl.mockResolvedValue(undefined);
    vi.spyOn(window, "prompt").mockReturnValue("3");
    const user = userEvent.setup();

    renderPage();
    await screen.findByText("Engineering");
    await user.click(
      screen.getByRole("button", {
        name: /provision warm spares for engineering/i,
      }),
    );
    expect(
      await screen.findByText(/provisioning 3 desktop/i),
    ).toBeDefined();
    expect(clientMock.postImpl).toHaveBeenCalledWith(
      "/api/v1/pools/eng/provision",
      { count: 3 },
    );
  });

  it("drain confirms via window.confirm", async () => {
    setupGet({
      pools: [makePool("eng", "Engineering")],
      clusters: [makeCluster("c1", "pve-prod")],
    });
    clientMock.postImpl.mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();

    renderPage();
    await screen.findByText("Engineering");
    await user.click(
      screen.getByRole("button", { name: /drain engineering/i }),
    );
    await waitFor(() => {
      expect(clientMock.postImpl).toHaveBeenCalledWith(
        "/api/v1/pools/eng/drain",
        {},
      );
    });
  });

  it("capacity column falls back to em-dash on capacity query failure", async () => {
    setupGet({
      pools: [makePool("eng", "Engineering")],
      clusters: [makeCluster("c1", "pve-prod")],
      capacity: new Error("boom"),
    });
    renderPage();
    await screen.findByText("Engineering");
    expect(screen.getByText("—/10")).toBeDefined();
  });
});
