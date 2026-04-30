import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ClusterFormPage } from "@/pages/admin";
import type { ClusterRead } from "@/types/admin";
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

function existingCluster(): ClusterRead {
  return {
    id: "abc",
    name: "pve1",
    provider_type: "proxmox",
    api_url: "https://pve1.example.com:8006",
    token_id: "openvdi@pve!t1",
    verify_ssl: true,
    node_filter: null,
    provider_config: {},
    status: "active",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
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
          <Route path="/admin/clusters" element={<div>LIST</div>} />
          <Route path="/admin/clusters/new" element={<ClusterFormPage />} />
          <Route
            path="/admin/clusters/:id/edit"
            element={<ClusterFormPage />}
          />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("ClusterFormPage — create mode", () => {
  beforeEach(() => {
    clientMock.getImpl.mockReset();
    clientMock.postImpl.mockReset();
    clientMock.putImpl.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders empty form", () => {
    renderForm("/admin/clusters/new");
    expect(
      screen.getByRole("heading", { level: 1, name: /add cluster/i }),
    ).toBeDefined();
    expect(
      (screen.getByLabelText(/^name/i) as HTMLInputElement).value,
    ).toBe("");
  });

  it("submits ClusterCreate with all fields", async () => {
    clientMock.postImpl.mockResolvedValue(existingCluster());
    const user = userEvent.setup();
    renderForm("/admin/clusters/new");

    await user.type(screen.getByLabelText(/^name/i), "pve1");
    await user.type(
      screen.getByLabelText(/api url/i),
      "https://pve1.example.com:8006",
    );
    await user.type(
      screen.getByLabelText(/token id/i),
      "openvdi@pve!t1",
    );
    await user.type(screen.getByLabelText(/token secret/i), "s3cret");
    await user.click(screen.getByRole("button", { name: /create cluster/i }));

    await waitFor(() => {
      expect(clientMock.postImpl).toHaveBeenCalledTimes(1);
    });
    const [path, body] = clientMock.postImpl.mock.calls[0]!;
    expect(path).toBe("/api/v1/clusters");
    expect(body).toMatchObject({
      name: "pve1",
      api_url: "https://pve1.example.com:8006",
      token_id: "openvdi@pve!t1",
      token_secret: "s3cret",
      verify_ssl: true,
    });
  });

  it("displays submit errors via brokerErrorCode mapping", async () => {
    clientMock.postImpl.mockRejectedValue(
      new BrokerError({
        httpStatus: 502,
        code: "PROVIDER_ERROR",
        message: "unreachable",
        envelope: null,
      }),
    );
    const user = userEvent.setup();
    renderForm("/admin/clusters/new");

    await user.type(screen.getByLabelText(/^name/i), "pve1");
    await user.type(
      screen.getByLabelText(/api url/i),
      "https://x:8006",
    );
    await user.type(screen.getByLabelText(/token id/i), "x@pve!y");
    await user.type(screen.getByLabelText(/token secret/i), "s");
    await user.click(screen.getByRole("button", { name: /create cluster/i }));

    expect(
      await screen.findByText(/Couldn't reach the cluster/i),
    ).toBeDefined();
  });
});

describe("ClusterFormPage — edit mode", () => {
  beforeEach(() => {
    clientMock.getImpl.mockReset();
    clientMock.postImpl.mockReset();
    clientMock.putImpl.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("pre-populates form from useClusterDetailQuery", async () => {
    clientMock.getImpl.mockResolvedValue(existingCluster());
    renderForm("/admin/clusters/abc/edit");
    await waitFor(() => {
      expect(
        (screen.getByLabelText(/^name/i) as HTMLInputElement).value,
      ).toBe("pve1");
    });
    expect(
      (screen.getByLabelText(/api url/i) as HTMLInputElement).value,
    ).toBe("https://pve1.example.com:8006");
  });

  it("token_secret field renders empty in edit mode (FE8)", async () => {
    clientMock.getImpl.mockResolvedValue(existingCluster());
    renderForm("/admin/clusters/abc/edit");
    await waitFor(() => {
      expect(
        (screen.getByLabelText(/^name/i) as HTMLInputElement).value,
      ).toBe("pve1");
    });
    expect(
      (screen.getByLabelText(/token secret/i) as HTMLInputElement).value,
    ).toBe("");
  });

  it("submits ClusterUpdate WITHOUT token_secret when field is empty (FE8)", async () => {
    clientMock.getImpl.mockResolvedValue(existingCluster());
    clientMock.putImpl.mockResolvedValue(existingCluster());
    const user = userEvent.setup();
    renderForm("/admin/clusters/abc/edit");
    await waitFor(() => {
      expect(
        (screen.getByLabelText(/^name/i) as HTMLInputElement).value,
      ).toBe("pve1");
    });
    // Leave token_secret blank; just rename.
    await user.clear(screen.getByLabelText(/^name/i));
    await user.type(screen.getByLabelText(/^name/i), "pve1-renamed");
    await user.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => {
      expect(clientMock.putImpl).toHaveBeenCalledTimes(1);
    });
    const [path, body] = clientMock.putImpl.mock.calls[0]!;
    expect(path).toBe("/api/v1/clusters/abc");
    const payload = body as Record<string, unknown>;
    expect(payload["name"]).toBe("pve1-renamed");
    // FE8: empty input → key omitted (not sent as "" or null).
    expect("token_secret" in payload).toBe(false);
  });

  it("submits ClusterUpdate WITH token_secret when user types a new value (FE8)", async () => {
    clientMock.getImpl.mockResolvedValue(existingCluster());
    clientMock.putImpl.mockResolvedValue(existingCluster());
    const user = userEvent.setup();
    renderForm("/admin/clusters/abc/edit");
    await waitFor(() => {
      expect(
        (screen.getByLabelText(/^name/i) as HTMLInputElement).value,
      ).toBe("pve1");
    });
    await user.type(screen.getByLabelText(/token secret/i), "newSecret");
    await user.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => {
      expect(clientMock.putImpl).toHaveBeenCalledTimes(1);
    });
    const [, body] = clientMock.putImpl.mock.calls[0]!;
    expect((body as Record<string, unknown>)["token_secret"]).toBe(
      "newSecret",
    );
  });
});
