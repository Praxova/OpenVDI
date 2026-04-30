import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { ClustersPage } from "@/pages/admin";
import type { ClusterRead } from "@/types/admin";
import { BrokerError } from "@/api/errors";

// Hoisted mock so tests can vary the get/delete behavior per case.
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

function makeCluster(
  name: string,
  status: ClusterRead["status"] = "active",
): ClusterRead {
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
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <MemoryRouter initialEntries={["/admin/clusters"]}>
      <QueryClientProvider client={qc}>
        <ClustersPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("ClustersPage", () => {
  beforeEach(() => {
    clientMock.getImpl.mockReset();
    clientMock.deleteImpl.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the cluster list with Add button", async () => {
    clientMock.getImpl.mockResolvedValue([
      makeCluster("pve1", "active"),
      makeCluster("pve2", "offline"),
    ]);
    renderPage();
    expect(await screen.findByText("pve1")).toBeDefined();
    expect(screen.getByText("pve2")).toBeDefined();
    expect(screen.getByRole("link", { name: /add cluster/i })).toBeDefined();
  });

  it("renders empty state when no clusters", async () => {
    clientMock.getImpl.mockResolvedValue([]);
    renderPage();
    expect(
      await screen.findByText(/No clusters registered yet/i),
    ).toBeDefined();
  });

  it("renders error state on query failure", async () => {
    clientMock.getImpl.mockRejectedValue(new Error("boom"));
    renderPage();
    expect(
      await screen.findByText(/Couldn't load the list/i),
    ).toBeDefined();
  });

  it("Add cluster link points to /admin/clusters/new", async () => {
    clientMock.getImpl.mockResolvedValue([]);
    renderPage();
    const link = await screen.findByRole("link", { name: /add cluster/i });
    expect(link.getAttribute("href")).toBe("/admin/clusters/new");
  });

  it("delete confirms via window.confirm and invokes mutation", async () => {
    clientMock.getImpl.mockResolvedValue([makeCluster("pve1")]);
    clientMock.deleteImpl.mockResolvedValue(undefined);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();

    renderPage();
    await screen.findByText("pve1");
    await user.click(screen.getByRole("button", { name: /delete pve1/i }));

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(clientMock.deleteImpl).toHaveBeenCalledWith(
        "/api/v1/clusters/pve1",
      );
    });
  });

  it("delete is canceled when user clicks 'Cancel' in confirm", async () => {
    clientMock.getImpl.mockResolvedValue([makeCluster("pve1")]);
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();

    renderPage();
    await screen.findByText("pve1");
    await user.click(screen.getByRole("button", { name: /delete pve1/i }));

    expect(clientMock.deleteImpl).not.toHaveBeenCalled();
  });

  it("delete shows inline error on 409 (cluster has pools)", async () => {
    clientMock.getImpl.mockResolvedValue([makeCluster("pve1")]);
    clientMock.deleteImpl.mockRejectedValue(
      new BrokerError({
        httpStatus: 409,
        code: "CONFLICT",
        message: "pools reference cluster",
        envelope: null,
      }),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();

    renderPage();
    await screen.findByText("pve1");
    await user.click(screen.getByRole("button", { name: /delete pve1/i }));

    expect(
      await screen.findByText(/has pools assigned/i),
    ).toBeDefined();
  });
});
