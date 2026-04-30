import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { TemplatesPage } from "@/pages/admin";
import type { ClusterRead, TemplateRead } from "@/types/admin";
import { BrokerError } from "@/api/errors";


const clientMock = vi.hoisted(() => ({
  getImpl: vi.fn(),
  deleteImpl: vi.fn(),
}));

vi.mock("@/api/client", () => ({
  useBrokerClient: () => ({
    get: clientMock.getImpl,
    delete: clientMock.deleteImpl,
    post: vi.fn(),
    put: vi.fn(),
  }),
}));


function makeTemplate(name: string, clusterId: string): TemplateRead {
  return {
    id: name,
    cluster_id: clusterId,
    name,
    pve_vmid: 9001,
    pve_node: "pve1",
    os_type: "windows11",
    description: null,
    cpu_cores: 2,
    memory_mb: 4096,
    disk_gb: 60,
    gpu_required: false,
    tags: [],
    provider_config: {},
    status: "active",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

function makeCluster(id: string, name: string): ClusterRead {
  return {
    id,
    name,
    provider_type: "proxmox",
    api_url: "https://test:8006",
    token_id: "x@pve!y",
    verify_ssl: true,
    node_filter: null,
    provider_config: {},
    status: "active",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

function setupGet(opts: {
  templates?: TemplateRead[] | Error;
  clusters?: ClusterRead[] | Error;
}) {
  clientMock.getImpl.mockImplementation(async (path: string) => {
    if (path.startsWith("/api/v1/templates")) {
      const v = opts.templates;
      if (v instanceof Error) throw v;
      return v ?? [];
    }
    if (path.startsWith("/api/v1/clusters")) {
      const v = opts.clusters;
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
    <MemoryRouter initialEntries={["/admin/templates"]}>
      <QueryClientProvider client={qc}>
        <TemplatesPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("TemplatesPage", () => {
  beforeEach(() => {
    clientMock.getImpl.mockReset();
    clientMock.deleteImpl.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the template list with cluster names resolved", async () => {
    setupGet({
      templates: [makeTemplate("win11-base", "cluster-1")],
      clusters: [makeCluster("cluster-1", "pve-prod")],
    });
    renderPage();
    expect(await screen.findByText("win11-base")).toBeDefined();
    // Cluster name (not UUID) shown.
    expect(await screen.findByText("pve-prod")).toBeDefined();
    expect(screen.queryByText("cluster-1")).toBeNull();
  });

  it("renders empty state when no templates", async () => {
    setupGet({ templates: [], clusters: [] });
    renderPage();
    expect(
      await screen.findByText(/No templates registered yet/i),
    ).toBeDefined();
  });

  it("falls back to UUID prefix when clusters query fails", async () => {
    setupGet({
      templates: [makeTemplate("win11-base", "abcd1234efgh")],
      clusters: new Error("boom"),
    });
    renderPage();
    expect(await screen.findByText("win11-base")).toBeDefined();
    // First 8 chars of UUID-like cluster_id.
    expect(screen.getByText("abcd1234")).toBeDefined();
  });

  it("delete confirms via window.confirm and invokes mutation", async () => {
    setupGet({
      templates: [makeTemplate("win11-base", "c1")],
      clusters: [makeCluster("c1", "pve-prod")],
    });
    clientMock.deleteImpl.mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();

    renderPage();
    await screen.findByText("win11-base");
    await user.click(
      screen.getByRole("button", { name: /delete win11-base/i }),
    );

    await waitFor(() => {
      expect(clientMock.deleteImpl).toHaveBeenCalledWith(
        "/api/v1/templates/win11-base",
      );
    });
  });

  it("delete shows inline error on 409", async () => {
    setupGet({
      templates: [makeTemplate("win11-base", "c1")],
      clusters: [makeCluster("c1", "pve-prod")],
    });
    clientMock.deleteImpl.mockRejectedValue(
      new BrokerError({
        httpStatus: 409,
        code: "CONFLICT",
        message: "pools",
        envelope: null,
      }),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();

    renderPage();
    await screen.findByText("win11-base");
    await user.click(
      screen.getByRole("button", { name: /delete win11-base/i }),
    );

    expect(
      await screen.findByText(/referenced by one or more pools/i),
    ).toBeDefined();
  });
});
