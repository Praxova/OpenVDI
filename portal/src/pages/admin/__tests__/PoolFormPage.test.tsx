import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { PoolFormPage } from "@/pages/admin";
import { BrokerError } from "@/api/errors";
import type {
  ClusterRead,
  PoolRead,
  PoolStatus,
  PoolType,
  TemplateRead,
} from "@/types/admin";


const clientMock = vi.hoisted(() => ({
  getImpl: vi.fn(),
  postImpl: vi.fn(),
  putImpl: vi.fn(),
  deleteImpl: vi.fn(),
}));

vi.mock("@/api/client", () => ({
  useBrokerClient: () => ({
    get: clientMock.getImpl,
    post: clientMock.postImpl,
    put: clientMock.putImpl,
    delete: clientMock.deleteImpl,
  }),
}));


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

function makeTemplate(id: string, name: string, clusterId: string): TemplateRead {
  return {
    id,
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

function existingPool(): PoolRead {
  return {
    id: "p1",
    name: "engineering",
    display_name: "Engineering",
    description: "Eng team pool",
    pool_type: "nonpersistent" as PoolType,
    template_id: "tpl-1",
    cluster_id: "c1",
    min_spare: 2,
    max_size: 10,
    vmid_range_start: 5000,
    vmid_range_end: 5099,
    name_prefix: "ENG",
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
  };
}

function setupGet(opts: {
  pool?: PoolRead | Error;
  clusters?: ClusterRead[];
  templates?: TemplateRead[];
  entitlements?: unknown[];
}) {
  clientMock.getImpl.mockImplementation(async (path: string) => {
    if (path.match(/^\/api\/v1\/pools\/[^/]+$/)) {
      const v = opts.pool;
      if (v === undefined) throw new Error(`no pool fixture for ${path}`);
      if (v instanceof Error) throw v;
      return v;
    }
    if (path.includes("/entitlements")) {
      return opts.entitlements ?? [];
    }
    if (path.startsWith("/api/v1/clusters")) {
      return opts.clusters ?? [];
    }
    if (path.startsWith("/api/v1/templates")) {
      return opts.templates ?? [];
    }
    throw new Error(`unexpected path: ${path}`);
  });
}

function renderForm(initialPath: string) {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <QueryClientProvider client={qc}>
        <Routes>
          <Route path="/admin/pools" element={<div>LIST</div>} />
          <Route path="/admin/pools/new" element={<PoolFormPage />} />
          <Route
            path="/admin/pools/:id/edit"
            element={<PoolFormPage />}
          />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("PoolFormPage — create mode", () => {
  beforeEach(() => {
    Object.values(clientMock).forEach((m) => m.mockReset());
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders empty form with cluster + template dropdowns", async () => {
    setupGet({
      clusters: [makeCluster("c1", "pve-prod")],
      templates: [makeTemplate("tpl-1", "win11-base", "c1")],
    });
    renderForm("/admin/pools/new");
    expect(
      await screen.findByRole("heading", {
        level: 1, name: /add pool/i,
      }),
    ).toBeDefined();
    expect(await screen.findByLabelText(/cluster/i)).toBeDefined();
    expect(screen.getByLabelText(/template/i)).toBeDefined();
  });

  it("template dropdown filters by selected cluster", async () => {
    setupGet({
      clusters: [
        makeCluster("c1", "pve-prod"),
        makeCluster("c2", "pve-dr"),
      ],
      templates: [
        makeTemplate("t1", "win11-prod", "c1"),
        makeTemplate("t2", "win11-dr", "c2"),
      ],
    });
    const user = userEvent.setup();
    renderForm("/admin/pools/new");

    const cluster = await screen.findByLabelText(/cluster/i);
    // Wait for cluster options to populate from useClustersQuery.
    await screen.findByRole("option", { name: "pve-prod" });
    await user.selectOptions(cluster, "c1");
    // After picking c1, only c1's templates appear.
    const tpl = screen.getByLabelText(/template/i) as HTMLSelectElement;
    const opts = Array.from(tpl.options).map((o) => o.value);
    expect(opts).toContain("t1");
    expect(opts).not.toContain("t2");
  });

  it("submits PoolCreate with parsed numerics", async () => {
    setupGet({
      clusters: [makeCluster("c1", "pve-prod")],
      templates: [makeTemplate("tpl-1", "win11-base", "c1")],
    });
    clientMock.postImpl.mockResolvedValue(existingPool());
    const user = userEvent.setup();
    renderForm("/admin/pools/new");

    const cluster = await screen.findByLabelText(/cluster/i);
    // Wait for cluster options to populate from useClustersQuery.
    await screen.findByRole("option", { name: "pve-prod" });
    await user.selectOptions(cluster, "c1");
    await user.selectOptions(screen.getByLabelText(/template/i), "tpl-1");
    await user.type(screen.getByLabelText(/name \(slug\)/i), "engineering");
    await user.type(screen.getByLabelText(/display name/i), "Engineering");
    await user.type(screen.getByLabelText(/vmid range start/i), "5000");
    await user.type(screen.getByLabelText(/vmid range end/i), "5099");
    await user.type(screen.getByLabelText(/name prefix/i), "ENG");
    await user.click(
      screen.getByRole("button", { name: /create pool/i }),
    );

    await waitFor(() => {
      expect(clientMock.postImpl).toHaveBeenCalledTimes(1);
    });
    const [path, body] = clientMock.postImpl.mock.calls[0]!;
    expect(path).toBe("/api/v1/pools");
    expect(body).toMatchObject({
      cluster_id: "c1",
      template_id: "tpl-1",
      pool_type: "nonpersistent",
      name: "engineering",
      display_name: "Engineering",
      vmid_range_start: 5000, // parsed to number
      vmid_range_end: 5099,
      name_prefix: "ENG",
      min_spare: 1,
      max_size: 10,
    });
  });

  it("displays 409 (range overlap) error message", async () => {
    setupGet({
      clusters: [makeCluster("c1", "pve-prod")],
      templates: [makeTemplate("tpl-1", "win11-base", "c1")],
    });
    clientMock.postImpl.mockRejectedValue(
      new BrokerError({
        httpStatus: 409,
        code: "CONFLICT",
        message: "range overlap",
        envelope: null,
      }),
    );
    const user = userEvent.setup();
    renderForm("/admin/pools/new");

    const cluster = await screen.findByLabelText(/cluster/i);
    // Wait for cluster options to populate from useClustersQuery.
    await screen.findByRole("option", { name: "pve-prod" });
    await user.selectOptions(cluster, "c1");
    await user.selectOptions(screen.getByLabelText(/template/i), "tpl-1");
    await user.type(screen.getByLabelText(/name \(slug\)/i), "x");
    await user.type(screen.getByLabelText(/display name/i), "X");
    await user.type(screen.getByLabelText(/vmid range start/i), "5000");
    await user.type(screen.getByLabelText(/vmid range end/i), "5099");
    await user.type(screen.getByLabelText(/name prefix/i), "X");
    await user.click(
      screen.getByRole("button", { name: /create pool/i }),
    );

    expect(
      await screen.findByText(/VMID range overlaps/i),
    ).toBeDefined();
  });
});


describe("PoolFormPage — edit mode", () => {
  beforeEach(() => {
    Object.values(clientMock).forEach((m) => m.mockReset());
  });
  afterEach(() => vi.restoreAllMocks());

  it("pre-populates form from usePoolDetailQuery", async () => {
    setupGet({
      pool: existingPool(),
      clusters: [makeCluster("c1", "pve-prod")],
      templates: [makeTemplate("tpl-1", "win11-base", "c1")],
    });
    renderForm("/admin/pools/p1/edit");
    await waitFor(() => {
      expect(
        (screen.getByLabelText(/display name/i) as HTMLInputElement).value,
      ).toBe("Engineering");
    });
    expect(
      (screen.getByLabelText(/name \(slug\)/i) as HTMLInputElement).value,
    ).toBe("engineering");
  });

  it("renders cluster, template, pool type, vmid range, as read-only", async () => {
    setupGet({
      pool: existingPool(),
      clusters: [makeCluster("c1", "pve-prod")],
      templates: [makeTemplate("tpl-1", "win11-base", "c1")],
    });
    renderForm("/admin/pools/p1/edit");
    await waitFor(() => {
      expect(
        (screen.getByLabelText(/display name/i) as HTMLInputElement).value,
      ).toBe("Engineering");
    });
    expect(
      (screen.getByLabelText(/cluster/i) as HTMLInputElement).disabled,
    ).toBe(true);
    expect(
      (screen.getByLabelText(/template/i) as HTMLInputElement).disabled,
    ).toBe(true);
    expect(
      (screen.getByLabelText(/pool type/i) as HTMLInputElement).disabled,
    ).toBe(true);
    expect(
      (screen.getByLabelText(/vmid range start/i) as HTMLInputElement).disabled,
    ).toBe(true);
    expect(
      (screen.getByLabelText(/vmid range end/i) as HTMLInputElement).disabled,
    ).toBe(true);
  });

  it("PUT payload excludes immutable fields", async () => {
    setupGet({
      pool: existingPool(),
      clusters: [makeCluster("c1", "pve-prod")],
      templates: [makeTemplate("tpl-1", "win11-base", "c1")],
    });
    clientMock.putImpl.mockResolvedValue(existingPool());
    const user = userEvent.setup();
    renderForm("/admin/pools/p1/edit");
    await waitFor(() => {
      expect(
        (screen.getByLabelText(/display name/i) as HTMLInputElement).value,
      ).toBe("Engineering");
    });
    await user.click(
      screen.getByRole("button", { name: /save changes/i }),
    );
    await waitFor(() => {
      expect(clientMock.putImpl).toHaveBeenCalledTimes(1);
    });
    const [path, body] = clientMock.putImpl.mock.calls[0]!;
    expect(path).toBe("/api/v1/pools/p1");
    const payload = body as Record<string, unknown>;
    expect("cluster_id" in payload).toBe(false);
    expect("template_id" in payload).toBe(false);
    expect("pool_type" in payload).toBe(false);
    expect("vmid_range_start" in payload).toBe(false);
    expect("vmid_range_end" in payload).toBe(false);
    expect(payload["name"]).toBe("engineering");
  });

  it("status field appears in edit mode only", async () => {
    setupGet({
      pool: existingPool(),
      clusters: [makeCluster("c1", "pve-prod")],
      templates: [makeTemplate("tpl-1", "win11-base", "c1")],
    });
    renderForm("/admin/pools/p1/edit");
    await waitFor(() => {
      expect(
        (screen.getByLabelText(/display name/i) as HTMLInputElement).value,
      ).toBe("Engineering");
    });
    expect(screen.getByLabelText(/^status/i)).toBeDefined();
  });

  it("status field is hidden in create mode", async () => {
    setupGet({
      clusters: [makeCluster("c1", "pve-prod")],
      templates: [makeTemplate("tpl-1", "win11-base", "c1")],
    });
    renderForm("/admin/pools/new");
    await screen.findByLabelText(/cluster/i);
    expect(screen.queryByLabelText(/^status/i)).toBeNull();
  });

  it("empty cpu_cores → null in payload (not '')", async () => {
    setupGet({
      pool: existingPool(),
      clusters: [makeCluster("c1", "pve-prod")],
      templates: [makeTemplate("tpl-1", "win11-base", "c1")],
    });
    clientMock.putImpl.mockResolvedValue(existingPool());
    const user = userEvent.setup();
    renderForm("/admin/pools/p1/edit");
    await waitFor(() => {
      expect(
        (screen.getByLabelText(/display name/i) as HTMLInputElement).value,
      ).toBe("Engineering");
    });
    await user.click(
      screen.getByRole("button", { name: /save changes/i }),
    );
    await waitFor(() => {
      expect(clientMock.putImpl).toHaveBeenCalledTimes(1);
    });
    const [, body] = clientMock.putImpl.mock.calls[0]!;
    const payload = body as Record<string, unknown>;
    expect(payload["cpu_cores"]).toBeNull();
    expect(payload["memory_mb"]).toBeNull();
  });

  it("pool_type=persistent disables refresh_on_logoff and delete_on_logoff", async () => {
    setupGet({
      pool: { ...existingPool(), pool_type: "persistent" as PoolType },
      clusters: [makeCluster("c1", "pve-prod")],
      templates: [makeTemplate("tpl-1", "win11-base", "c1")],
    });
    renderForm("/admin/pools/p1/edit");
    await waitFor(() => {
      expect(
        (screen.getByLabelText(/display name/i) as HTMLInputElement).value,
      ).toBe("Engineering");
    });
    expect(
      (screen.getByLabelText(/refresh on logoff/i) as HTMLInputElement)
        .disabled,
    ).toBe(true);
    expect(
      (screen.getByLabelText(/delete on logoff/i) as HTMLInputElement)
        .disabled,
    ).toBe(true);
  });
});
