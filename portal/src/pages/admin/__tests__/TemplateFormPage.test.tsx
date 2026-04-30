import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { TemplateFormPage } from "@/pages/admin";
import type {
  ClusterRead,
  TemplateRead,
  TemplateValidationResult,
} from "@/types/admin";
import { BrokerError } from "@/api/errors";


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

function existingTemplate(): TemplateRead {
  return {
    id: "tpl-1",
    cluster_id: "c1",
    name: "win11-base",
    pve_vmid: 9001,
    pve_node: "pve1",
    os_type: "windows11",
    description: "Golden Win11",
    cpu_cores: 4,
    memory_mb: 8192,
    disk_gb: 80,
    gpu_required: false,
    tags: [],
    provider_config: {},
    status: "active",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

function setupGet(opts: {
  template?: TemplateRead | Error;
  clusters?: ClusterRead[] | Error;
}) {
  clientMock.getImpl.mockImplementation(async (path: string) => {
    if (path.includes("/templates/") && !path.endsWith("validate")) {
      const v = opts.template;
      if (v === undefined) throw new Error(`no template fixture for ${path}`);
      if (v instanceof Error) throw v;
      return v;
    }
    if (path.startsWith("/api/v1/clusters")) {
      const v = opts.clusters;
      if (v instanceof Error) throw v;
      return v ?? [];
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
          <Route path="/admin/templates" element={<div>LIST</div>} />
          <Route
            path="/admin/templates/new"
            element={<TemplateFormPage />}
          />
          <Route
            path="/admin/templates/:id/edit"
            element={<TemplateFormPage />}
          />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

// ── Create mode ──────────────────────────────────────────────


describe("TemplateFormPage — create mode", () => {
  beforeEach(() => {
    Object.values(clientMock).forEach((m) => m.mockReset());
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders empty form with cluster dropdown populated", async () => {
    setupGet({ clusters: [makeCluster("c1", "pve-prod")] });
    renderForm("/admin/templates/new");
    expect(
      await screen.findByRole("heading", {
        level: 1, name: /add template/i,
      }),
    ).toBeDefined();
    const select = await screen.findByLabelText(/cluster/i);
    expect((select as HTMLSelectElement).value).toBe("");
    expect(screen.getByText("pve-prod")).toBeDefined();
  });

  it("disables submit when no clusters exist + shows callout", async () => {
    setupGet({ clusters: [] });
    renderForm("/admin/templates/new");
    expect(
      await screen.findByText(/need at least one cluster/i),
    ).toBeDefined();
    const submit = screen.getByRole("button", { name: /create template/i });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
  });

  it("submits TemplateCreate with parsed numerics", async () => {
    setupGet({ clusters: [makeCluster("c1", "pve-prod")] });
    clientMock.postImpl.mockResolvedValue(existingTemplate());
    const user = userEvent.setup();
    renderForm("/admin/templates/new");

    // Wait for the form (not just the FormPageShell heading) by
    // requiring the cluster select to be rendered.
    const clusterSelect = await screen.findByLabelText(/cluster/i);
    await user.selectOptions(clusterSelect, "c1");
    await user.type(screen.getByLabelText(/^name/i), "win11-base");
    await user.type(screen.getByLabelText(/^vmid/i), "9001");
    await user.type(screen.getByLabelText(/^node/i), "pve1");
    // OS type defaults to windows11 — leave alone.
    await user.click(
      screen.getByRole("button", { name: /create template/i }),
    );

    await waitFor(() => {
      expect(clientMock.postImpl).toHaveBeenCalledTimes(1);
    });
    const [path, body] = clientMock.postImpl.mock.calls[0]!;
    expect(path).toBe("/api/v1/templates");
    expect(body).toMatchObject({
      cluster_id: "c1",
      name: "win11-base",
      pve_vmid: 9001, // parsed to number
      pve_node: "pve1",
      os_type: "windows11",
      cpu_cores: 2,
      memory_mb: 4096,
      disk_gb: 60,
      gpu_required: false,
    });
  });

  it("displays submit errors via brokerErrorCode mapping", async () => {
    setupGet({ clusters: [makeCluster("c1", "pve-prod")] });
    clientMock.postImpl.mockRejectedValue(
      new BrokerError({
        httpStatus: 502,
        code: "PROVIDER_ERROR",
        message: "unreachable",
        envelope: null,
      }),
    );
    const user = userEvent.setup();
    renderForm("/admin/templates/new");

    const clusterSelect = await screen.findByLabelText(/cluster/i);
    await user.selectOptions(clusterSelect, "c1");
    await user.type(screen.getByLabelText(/^name/i), "x");
    await user.type(screen.getByLabelText(/^vmid/i), "1");
    await user.type(screen.getByLabelText(/^node/i), "pve1");
    await user.click(
      screen.getByRole("button", { name: /create template/i }),
    );

    expect(
      await screen.findByText(/Couldn't reach the cluster/i),
    ).toBeDefined();
  });
});


// ── Edit mode ────────────────────────────────────────────────


describe("TemplateFormPage — edit mode", () => {
  beforeEach(() => {
    Object.values(clientMock).forEach((m) => m.mockReset());
  });
  afterEach(() => vi.restoreAllMocks());

  it("pre-populates form from useTemplateDetailQuery", async () => {
    setupGet({
      template: existingTemplate(),
      clusters: [makeCluster("c1", "pve-prod")],
    });
    renderForm("/admin/templates/tpl-1/edit");
    await waitFor(() => {
      expect(
        (screen.getByLabelText(/^name/i) as HTMLInputElement).value,
      ).toBe("win11-base");
    });
    expect(
      (screen.getByLabelText(/^node/i) as HTMLInputElement).value,
    ).toBe("pve1");
  });

  it("renders cluster_id and pve_vmid as read-only", async () => {
    setupGet({
      template: existingTemplate(),
      clusters: [makeCluster("c1", "pve-prod")],
    });
    renderForm("/admin/templates/tpl-1/edit");
    await waitFor(() => {
      expect(
        (screen.getByLabelText(/^name/i) as HTMLInputElement).value,
      ).toBe("win11-base");
    });
    const cluster = screen.getByLabelText(/cluster/i) as HTMLInputElement;
    const vmid = screen.getByLabelText(/^vmid/i) as HTMLInputElement;
    expect(cluster.disabled).toBe(true);
    expect(vmid.disabled).toBe(true);
    // Cluster shows the resolved name, not the UUID.
    expect(cluster.value).toBe("pve-prod");
  });

  it("excludes cluster_id and pve_vmid from PUT payload", async () => {
    setupGet({
      template: existingTemplate(),
      clusters: [makeCluster("c1", "pve-prod")],
    });
    clientMock.putImpl.mockResolvedValue(existingTemplate());
    const user = userEvent.setup();
    renderForm("/admin/templates/tpl-1/edit");
    await waitFor(() => {
      expect(
        (screen.getByLabelText(/^name/i) as HTMLInputElement).value,
      ).toBe("win11-base");
    });
    await user.click(
      screen.getByRole("button", { name: /save changes/i }),
    );

    await waitFor(() => {
      expect(clientMock.putImpl).toHaveBeenCalledTimes(1);
    });
    const [path, body] = clientMock.putImpl.mock.calls[0]!;
    expect(path).toBe("/api/v1/templates/tpl-1");
    const payload = body as Record<string, unknown>;
    expect("cluster_id" in payload).toBe(false);
    expect("pve_vmid" in payload).toBe(false);
    expect(payload["name"]).toBe("win11-base");
  });
});


// ── Validate ─────────────────────────────────────────────────


describe("TemplateFormPage — validate", () => {
  beforeEach(() => {
    Object.values(clientMock).forEach((m) => m.mockReset());
  });
  afterEach(() => vi.restoreAllMocks());

  it("validate panel is hidden in create mode", async () => {
    setupGet({ clusters: [makeCluster("c1", "pve-prod")] });
    renderForm("/admin/templates/new");
    // Wait until the form (not the loading state) is rendered.
    await screen.findByLabelText(/cluster/i);
    expect(
      screen.queryByRole("heading", { name: /^validation$/i }),
    ).toBeNull();
  });

  it("clicking Run validation calls useValidateTemplateMutation", async () => {
    setupGet({
      template: existingTemplate(),
      clusters: [makeCluster("c1", "pve-prod")],
    });
    const result: TemplateValidationResult = {
      template_id: "tpl-1",
      passed: true,
      checks: [
        { name: "exists", passed: true, message: "VM 9001 exists" },
      ],
    };
    clientMock.postImpl.mockResolvedValue(result);
    const user = userEvent.setup();
    renderForm("/admin/templates/tpl-1/edit");
    await waitFor(() => {
      expect(
        (screen.getByLabelText(/^name/i) as HTMLInputElement).value,
      ).toBe("win11-base");
    });
    await user.click(
      screen.getByRole("button", { name: /run validation/i }),
    );

    await waitFor(() => {
      expect(clientMock.postImpl).toHaveBeenCalledWith(
        "/api/v1/templates/tpl-1/validate",
        {},
      );
    });
  });

  it("renders pass result with green badge", async () => {
    setupGet({
      template: existingTemplate(),
      clusters: [makeCluster("c1", "pve-prod")],
    });
    clientMock.postImpl.mockResolvedValue({
      template_id: "tpl-1",
      passed: true,
      checks: [
        { name: "exists", passed: true, message: "VM 9001 exists" },
        { name: "is_template", passed: true, message: "VM is a template" },
      ],
    });
    const user = userEvent.setup();
    renderForm("/admin/templates/tpl-1/edit");
    await waitFor(() => {
      expect(
        (screen.getByLabelText(/^name/i) as HTMLInputElement).value,
      ).toBe("win11-base");
    });
    await user.click(
      screen.getByRole("button", { name: /run validation/i }),
    );

    expect(
      await screen.findByText(/All checks passed/i),
    ).toBeDefined();
    expect(screen.getByText("exists")).toBeDefined();
    expect(screen.getByText("is_template")).toBeDefined();
  });

  it("renders fail result with red badge and per-check messages", async () => {
    setupGet({
      template: existingTemplate(),
      clusters: [makeCluster("c1", "pve-prod")],
    });
    clientMock.postImpl.mockResolvedValue({
      template_id: "tpl-1",
      passed: false,
      checks: [
        { name: "exists", passed: true, message: "VM 9001 exists" },
        {
          name: "is_template",
          passed: false,
          message: "VM 9001 is not configured as a template",
        },
      ],
    });
    const user = userEvent.setup();
    renderForm("/admin/templates/tpl-1/edit");
    await waitFor(() => {
      expect(
        (screen.getByLabelText(/^name/i) as HTMLInputElement).value,
      ).toBe("win11-base");
    });
    await user.click(
      screen.getByRole("button", { name: /run validation/i }),
    );

    expect(
      await screen.findByText(/Some checks failed/i),
    ).toBeDefined();
    expect(
      screen.getByText(/is not configured as a template/i),
    ).toBeDefined();
  });

  it("re-running replaces previous result", async () => {
    setupGet({
      template: existingTemplate(),
      clusters: [makeCluster("c1", "pve-prod")],
    });
    clientMock.postImpl
      .mockResolvedValueOnce({
        template_id: "tpl-1",
        passed: false,
        checks: [
          { name: "exists", passed: false, message: "VM 9001 not found" },
        ],
      })
      .mockResolvedValueOnce({
        template_id: "tpl-1",
        passed: true,
        checks: [
          { name: "exists", passed: true, message: "VM 9001 exists" },
        ],
      });
    const user = userEvent.setup();
    renderForm("/admin/templates/tpl-1/edit");
    await waitFor(() => {
      expect(
        (screen.getByLabelText(/^name/i) as HTMLInputElement).value,
      ).toBe("win11-base");
    });

    await user.click(
      screen.getByRole("button", { name: /run validation/i }),
    );
    expect(await screen.findByText(/Some checks failed/i)).toBeDefined();

    await user.click(
      screen.getByRole("button", { name: /re-run validation/i }),
    );
    expect(await screen.findByText(/All checks passed/i)).toBeDefined();
    expect(screen.queryByText(/Some checks failed/i)).toBeNull();
  });
});
