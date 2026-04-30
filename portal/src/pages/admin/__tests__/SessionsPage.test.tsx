import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import {
  SessionsPage,
  canForceDisconnect,
} from "@/pages/admin/SessionsPage";
import { presetToSince } from "@/api/admin/sessions";
import type {
  PoolRead,
  SessionReadAdmin,
  SessionStatus,
} from "@/types/admin";


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


function makeSession(
  id: string,
  username: string,
  opts: Partial<SessionReadAdmin> = {},
): SessionReadAdmin {
  return {
    id,
    desktop_id: "d1",
    desktop_name: "ENG-001",
    pool_id: "p1",
    pool_name: "Engineering",
    pool_type: "nonpersistent",
    username,
    protocol: "novnc",
    client_ip: "10.0.0.5",
    status: "active" as SessionStatus,
    connected_at: new Date().toISOString(),
    disconnected_at: null,
    ended_at: null,
    last_heartbeat: new Date().toISOString(),
    created_at: new Date().toISOString(),
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
  sessions?: SessionReadAdmin[] | Error;
  pools?: PoolRead[];
}) {
  clientMock.getImpl.mockImplementation(async (path: string) => {
    if (path.startsWith("/api/v1/sessions?")) {
      const v = opts.sessions;
      if (v instanceof Error) throw v;
      return v ?? [];
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
    <MemoryRouter initialEntries={["/admin/sessions"]}>
      <QueryClientProvider client={qc}>
        <SessionsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("SessionsPage", () => {
  beforeEach(() => {
    Object.values(clientMock).forEach((m) => m.mockReset());
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders list with pool name + desktop name resolved", async () => {
    setupGet({
      sessions: [makeSession("s1", "alice")],
      pools: [makePool("p1", "Engineering")],
    });
    renderPage();
    expect(await screen.findByText("alice")).toBeDefined();
    expect(screen.getByText("ENG-001")).toBeDefined();
    // Engineering appears in both filter dropdown and row → ≥2.
    expect(
      screen.getAllByText("Engineering").length,
    ).toBeGreaterThanOrEqual(2);
  });

  it("renders orphan sessions with '(deleted)' italics", async () => {
    setupGet({
      sessions: [
        makeSession("s1", "alice", {
          desktop_id: null,
          desktop_name: null,
          pool_id: null,
          pool_name: null,
        }),
      ],
      pools: [],
    });
    renderPage();
    expect(await screen.findByText("alice")).toBeDefined();
    expect(screen.getByText(/desktop deleted/i)).toBeDefined();
    expect(screen.getByText(/pool deleted/i)).toBeDefined();
  });

  it("empty state in live mode hints at toggling include_ended", async () => {
    setupGet({ sessions: [], pools: [] });
    renderPage();
    expect(
      await screen.findByText(/Toggle 'Include ended.disconnected' to see history/i),
    ).toBeDefined();
  });

  it("empty state in historical mode hints at filter relaxation", async () => {
    setupGet({ sessions: [], pools: [] });
    const user = userEvent.setup();
    renderPage();
    // Toggle the include_ended checkbox.
    await user.click(
      screen.getByRole("checkbox", { name: /include ended/i }),
    );
    expect(
      await screen.findByText(/in the selected range/i),
    ).toBeDefined();
  });

  it("renders error state with retry", async () => {
    setupGet({ sessions: new Error("boom"), pools: [] });
    renderPage();
    expect(
      await screen.findByText(/Couldn't load the list/i),
    ).toBeDefined();
  });

  it("user filter passes username query param", async () => {
    setupGet({
      sessions: [makeSession("s1", "alice")],
      pools: [],
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("alice");

    await user.type(screen.getByLabelText(/^user$/i), "bob");
    await waitFor(() => {
      const calls = clientMock.getImpl.mock.calls.map((c) => c[0] as string);
      expect(
        calls.some((p) => p.startsWith("/api/v1/sessions?") && p.includes("username=bob")),
      ).toBe(true);
    });
  });

  it("clicking a username populates the user filter input", async () => {
    setupGet({
      sessions: [makeSession("s1", "alice")],
      pools: [],
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("alice");

    await user.click(screen.getByRole("button", { name: "alice" }));
    const input = screen.getByLabelText(/^user$/i) as HTMLInputElement;
    await waitFor(() => {
      expect(input.value).toBe("alice");
    });
  });

  it("pool filter passes pool_id query param", async () => {
    setupGet({
      sessions: [makeSession("s1", "alice")],
      pools: [makePool("p1", "Engineering")],
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("alice");
    await user.selectOptions(screen.getByLabelText(/^pool$/i), "p1");
    await waitFor(() => {
      const calls = clientMock.getImpl.mock.calls.map((c) => c[0] as string);
      expect(
        calls.some((p) => p.startsWith("/api/v1/sessions?") && p.includes("pool_id=p1")),
      ).toBe(true);
    });
  });

  it("status filter passes status query param", async () => {
    setupGet({
      sessions: [makeSession("s1", "alice")],
      pools: [],
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("alice");
    await user.selectOptions(screen.getByLabelText(/^status$/i), "active");
    await waitFor(() => {
      const calls = clientMock.getImpl.mock.calls.map((c) => c[0] as string);
      expect(
        calls.some((p) => p.startsWith("/api/v1/sessions?") && p.includes("status=active")),
      ).toBe(true);
    });
  });

  it("toggling 'Include ended' enables the time-range dropdown", async () => {
    setupGet({ sessions: [], pools: [] });
    const user = userEvent.setup();
    renderPage();
    const trange = screen.getByLabelText(/time range/i) as HTMLSelectElement;
    expect(trange.disabled).toBe(true);
    await user.click(
      screen.getByRole("checkbox", { name: /include ended/i }),
    );
    expect(trange.disabled).toBe(false);
  });

  it("force-disconnect button visible only on connecting/active sessions", async () => {
    setupGet({
      sessions: [
        makeSession("s1", "alice", { status: "active" }),
        makeSession("s2", "bob", { status: "ended" }),
      ],
      pools: [],
    });
    renderPage();
    await screen.findByText("alice");
    expect(
      screen.queryByRole("button", { name: /force disconnect alice/i }),
    ).not.toBeNull();
    expect(
      screen.queryByRole("button", { name: /force disconnect bob/i }),
    ).toBeNull();
  });

  it("force-disconnect fires the mutation without window.confirm", async () => {
    setupGet({
      sessions: [makeSession("s1", "alice")],
      pools: [],
    });
    clientMock.deleteImpl.mockResolvedValue(undefined);
    const confirmSpy = vi.spyOn(window, "confirm");
    const user = userEvent.setup();

    renderPage();
    await screen.findByText("alice");
    await user.click(
      screen.getByRole("button", { name: /force disconnect alice/i }),
    );
    await waitFor(() => {
      expect(clientMock.deleteImpl).toHaveBeenCalledWith(
        "/api/v1/sessions/s1",
      );
    });
    // Per FE7: no confirm dialog.
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(
      await screen.findByText(/alice's session ended/i),
    ).toBeDefined();
  });
});


// ── Pure-function tests ─────────────────────────────────────


describe("SessionsPage predicates", () => {
  it("canForceDisconnect returns true for connecting/active only", () => {
    expect(canForceDisconnect("connecting")).toBe(true);
    expect(canForceDisconnect("active")).toBe(true);
    expect(canForceDisconnect("disconnected")).toBe(false);
    expect(canForceDisconnect("ended")).toBe(false);
  });

  it("presetToSince returns undefined for 'all'", () => {
    expect(presetToSince("all")).toBeUndefined();
  });

  it("presetToSince returns parseable ISO close to expected delta", () => {
    const now = Date.now();
    const t24 = Date.parse(presetToSince("24h")!);
    const t7 = Date.parse(presetToSince("7d")!);
    const t30 = Date.parse(presetToSince("30d")!);
    // ±2 seconds tolerance for clock skew during the test run.
    expect(Math.abs(now - 24 * 60 * 60 * 1000 - t24)).toBeLessThan(2000);
    expect(Math.abs(now - 7 * 24 * 60 * 60 * 1000 - t7)).toBeLessThan(2000);
    expect(Math.abs(now - 30 * 24 * 60 * 60 * 1000 - t30)).toBeLessThan(2000);
  });
});
