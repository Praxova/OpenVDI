import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import {
  DesktopsPage,
  canAssign,
  canDestroy,
  canRebuild,
  powerStateMatchesStatus,
} from "@/pages/admin/DesktopsPage";
import { BrokerError } from "@/api/errors";
import type {
  DesktopRead,
  DesktopStatus,
  PoolRead,
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


function makeDesktop(
  id: string,
  name: string,
  opts: Partial<DesktopRead> = {},
): DesktopRead {
  return {
    id,
    pool_id: "p1",
    pve_vmid: 5001,
    pve_node: "pve1",
    name,
    assigned_user: null,
    assignment_type: null,
    status: "available" as DesktopStatus,
    power_state: "stopped",
    last_connected: null,
    last_disconnected: null,
    provisioned_at: null,
    error_message: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...opts,
  };
}

function makePool(id: string, display: string): PoolRead {
  return {
    id,
    name: id,
    display_name: display,
    description: null,
    pool_type: "nonpersistent",
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
    status: "active",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

function setupGet(opts: {
  desktops?: DesktopRead[] | Error;
  pools?: PoolRead[];
}) {
  clientMock.getImpl.mockImplementation(async (path: string) => {
    if (path.startsWith("/api/v1/desktops?")) {
      const v = opts.desktops;
      if (v instanceof Error) throw v;
      return v ?? [];
    }
    if (path.match(/^\/api\/v1\/desktops\/[^/?]+$/)) {
      // detail (drawer); fall through with sensible defaults
      const list = opts.desktops;
      if (list instanceof Error || list === undefined || list.length === 0) {
        throw new Error("no desktop fixture");
      }
      const id = path.split("/").pop()!;
      const found = list.find((d) => d.id === id);
      if (found === undefined) throw new Error("not found");
      return { ...found, active_session: null, live_power_state: found.power_state };
    }
    if (path.startsWith("/api/v1/pools")) {
      return opts.pools ?? [];
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
    <MemoryRouter initialEntries={["/admin/desktops"]}>
      <QueryClientProvider client={qc}>
        <DesktopsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}


describe("DesktopsPage", () => {
  beforeEach(() => {
    Object.values(clientMock).forEach((m) => m.mockReset());
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders list with pool name resolved", async () => {
    setupGet({
      desktops: [makeDesktop("d1", "ENG-001", { pool_id: "p1" })],
      pools: [makePool("p1", "Engineering")],
    });
    renderPage();
    expect(await screen.findByText("ENG-001")).toBeDefined();
    // "Engineering" appears in both the pool-filter <option> and the
    // pool column for the row — assert both exist (≥2).
    expect(screen.getAllByText("Engineering").length).toBeGreaterThanOrEqual(2);
  });

  it("renders empty state for filtered-no-match", async () => {
    setupGet({ desktops: [], pools: [] });
    renderPage();
    expect(
      await screen.findByText(/No desktops match the current filters/i),
    ).toBeDefined();
  });

  it("renders error state with retry", async () => {
    setupGet({ desktops: new Error("boom"), pools: [] });
    renderPage();
    expect(
      await screen.findByText(/Couldn't load the list/i),
    ).toBeDefined();
  });

  it("pool filter passes pool_id query param to /desktops", async () => {
    setupGet({
      desktops: [makeDesktop("d1", "ENG-001")],
      pools: [makePool("p1", "Engineering")],
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("ENG-001");

    await user.selectOptions(
      screen.getByLabelText(/^pool$/i),
      "p1",
    );
    await waitFor(() => {
      const calls = clientMock.getImpl.mock.calls.map((c) => c[0] as string);
      const filtered = calls.find(
        (p) =>
          p.startsWith("/api/v1/desktops?") && p.includes("pool_id=p1"),
      );
      expect(filtered).toBeDefined();
    });
  });

  it("status filter passes status query param", async () => {
    setupGet({
      desktops: [makeDesktop("d1", "ENG-001")],
      pools: [makePool("p1", "Engineering")],
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("ENG-001");

    await user.selectOptions(
      screen.getByLabelText(/^status$/i),
      "available",
    );
    await waitFor(() => {
      const calls = clientMock.getImpl.mock.calls.map((c) => c[0] as string);
      expect(
        calls.some(
          (p) =>
            p.startsWith("/api/v1/desktops?") &&
            p.includes("status=available"),
        ),
      ).toBe(true);
    });
  });

  it("assigned-user free-text filter passes assigned_user query param", async () => {
    setupGet({
      desktops: [makeDesktop("d1", "ENG-001")],
      pools: [makePool("p1", "Engineering")],
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("ENG-001");

    await user.type(screen.getByLabelText(/assigned user/i), "alice");
    await waitFor(() => {
      const calls = clientMock.getImpl.mock.calls.map((c) => c[0] as string);
      expect(
        calls.some(
          (p) =>
            p.startsWith("/api/v1/desktops?") &&
            p.includes("assigned_user=alice"),
        ),
      ).toBe(true);
    });
  });

  it("assign button visible only on available/disconnected unassigned desktops", async () => {
    setupGet({
      desktops: [
        makeDesktop("d1", "READY", {
          status: "available",
          assigned_user: null,
        }),
        makeDesktop("d2", "ASSIGNED", {
          status: "assigned",
          assigned_user: "bob",
        }),
        makeDesktop("d3", "BUSY", {
          status: "connected",
          assigned_user: null,
        }),
      ],
      pools: [makePool("p1", "Engineering")],
    });
    renderPage();
    await screen.findByText("READY");

    // Match exact "Assign <name>" labels — "/^assign /i" excludes the
    // "Unassign ASSIGNED" button (which would otherwise match a loose
    // /assign/i).
    expect(
      screen.queryByRole("button", { name: /^assign ready$/i }),
    ).not.toBeNull();
    // d2 has an assigned user → unassign shows, assign hidden.
    expect(
      screen.queryByRole("button", { name: /^assign assigned$/i }),
    ).toBeNull();
    // d3 status=connected (not available/disconnected) → assign hidden.
    expect(
      screen.queryByRole("button", { name: /^assign busy$/i }),
    ).toBeNull();
  });

  it("unassign button visible only on assigned desktops", async () => {
    setupGet({
      desktops: [
        makeDesktop("d1", "ASSIGNED", { assigned_user: "alice" }),
        makeDesktop("d2", "FREE", { assigned_user: null }),
      ],
      pools: [makePool("p1", "Engineering")],
    });
    renderPage();
    await screen.findByText("ASSIGNED");

    expect(
      screen.queryByRole("button", { name: /unassign assigned/i }),
    ).not.toBeNull();
    expect(
      screen.queryByRole("button", { name: /unassign free/i }),
    ).toBeNull();
  });

  it("assign uses window.prompt and POSTs username", async () => {
    setupGet({
      desktops: [makeDesktop("d1", "ENG-001")],
      pools: [makePool("p1", "Engineering")],
    });
    clientMock.postImpl.mockResolvedValue(makeDesktop("d1", "ENG-001"));
    vi.spyOn(window, "prompt").mockReturnValue("alice");
    const user = userEvent.setup();

    renderPage();
    await screen.findByText("ENG-001");
    await user.click(
      screen.getByRole("button", { name: /assign eng-001/i }),
    );
    await waitFor(() => {
      expect(clientMock.postImpl).toHaveBeenCalledWith(
        "/api/v1/desktops/d1/assign",
        { username: "alice" },
      );
    });
  });

  it("assign 409 shows 'already holds a desktop' error", async () => {
    setupGet({
      desktops: [makeDesktop("d1", "ENG-001")],
      pools: [makePool("p1", "Engineering")],
    });
    clientMock.postImpl.mockRejectedValue(
      new BrokerError({
        httpStatus: 409,
        code: "CONFLICT",
        message: "already",
        envelope: null,
      }),
    );
    vi.spyOn(window, "prompt").mockReturnValue("alice");
    const user = userEvent.setup();

    renderPage();
    await screen.findByText("ENG-001");
    await user.click(
      screen.getByRole("button", { name: /assign eng-001/i }),
    );
    expect(
      await screen.findByText(/already holds a desktop in this pool/i),
    ).toBeDefined();
  });

  it("power menu opens on click and shows 4 actions", async () => {
    setupGet({
      desktops: [makeDesktop("d1", "ENG-001")],
      pools: [makePool("p1", "Engineering")],
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("ENG-001");

    await user.click(
      screen.getByRole("button", { name: /power actions for eng-001/i }),
    );
    expect(screen.getByRole("menu")).toBeDefined();
    expect(screen.getByRole("menuitem", { name: /^start$/i })).toBeDefined();
    expect(screen.getByRole("menuitem", { name: /^shutdown$/i })).toBeDefined();
    expect(screen.getByRole("menuitem", { name: /^reboot$/i })).toBeDefined();
    expect(
      screen.getByRole("menuitem", { name: /stop \(hard\)/i }),
    ).toBeDefined();
  });

  it("destroy confirms via window.confirm and shows success banner", async () => {
    setupGet({
      desktops: [makeDesktop("d1", "ENG-001")],
      pools: [makePool("p1", "Engineering")],
    });
    clientMock.deleteImpl.mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();

    renderPage();
    await screen.findByText("ENG-001");
    await user.click(
      screen.getByRole("button", { name: /destroy eng-001/i }),
    );
    await waitFor(() => {
      expect(clientMock.deleteImpl).toHaveBeenCalledWith(
        "/api/v1/desktops/d1",
      );
    });
    expect(
      await screen.findByText(/destroy kicked off/i),
    ).toBeDefined();
  });
});


// ── Pure-function tests ──────────────────────────────────────


describe("DesktopsPage predicates", () => {
  it("canAssign returns true for available/disconnected only", () => {
    expect(canAssign("available")).toBe(true);
    expect(canAssign("disconnected")).toBe(true);
    expect(canAssign("assigned")).toBe(false);
    expect(canAssign("connected")).toBe(false);
    expect(canAssign("provisioning")).toBe(false);
    expect(canAssign("error")).toBe(false);
  });

  it("canRebuild returns false for provisioning/deleting/connected", () => {
    expect(canRebuild("provisioning")).toBe(false);
    expect(canRebuild("deleting")).toBe(false);
    expect(canRebuild("connected")).toBe(false);
    expect(canRebuild("available")).toBe(true);
    expect(canRebuild("error")).toBe(true);
  });

  it("canDestroy returns false for provisioning/deleting", () => {
    expect(canDestroy("provisioning")).toBe(false);
    expect(canDestroy("deleting")).toBe(false);
    expect(canDestroy("available")).toBe(true);
    expect(canDestroy("connected")).toBe(true);
  });

  it("powerStateMatchesStatus flags connected+stopped as drift", () => {
    expect(powerStateMatchesStatus("connected", "stopped")).toBe(false);
    expect(powerStateMatchesStatus("connected", "running")).toBe(true);
    expect(powerStateMatchesStatus("deleting", "running")).toBe(false);
    expect(powerStateMatchesStatus("available", "stopped")).toBe(true);
  });
});
