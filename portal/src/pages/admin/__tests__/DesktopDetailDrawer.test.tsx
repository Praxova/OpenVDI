import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { DesktopDetailDrawer } from "@/pages/admin/DesktopDetailDrawer";
import type { DesktopReadDetailed, SessionRead } from "@/types/admin";


const clientMock = vi.hoisted(() => ({
  getImpl: vi.fn(),
}));

vi.mock("@/api/client", () => ({
  useBrokerClient: () => ({
    get: clientMock.getImpl,
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  }),
}));


function makeDetail(overrides: Partial<DesktopReadDetailed> = {}): DesktopReadDetailed {
  return {
    id: "d1",
    pool_id: "p1",
    pve_vmid: 5001,
    pve_node: "pve1",
    name: "ENG-001",
    assigned_user: "alice",
    assignment_type: "manual",
    status: "assigned",
    power_state: "running",
    last_connected: new Date().toISOString(),
    last_disconnected: null,
    provisioned_at: new Date().toISOString(),
    error_message: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    active_session: null,
    live_power_state: "running",
    ...overrides,
  };
}

function makeSession(): SessionRead {
  return {
    id: "s1",
    desktop_id: "d1",
    username: "alice",
    protocol: "novnc",
    client_ip: "10.0.0.5",
    status: "active",
    connected_at: new Date().toISOString(),
    disconnected_at: null,
    ended_at: null,
    os_user: "ALICE",
    os_info: null,
    vm_ip_address: "10.0.0.10",
    last_heartbeat: new Date().toISOString(),
    idle_since: null,
    created_at: new Date().toISOString(),
  };
}

function renderDrawer(
  desktopId: string | null,
  onClose: () => void = () => {},
) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <DesktopDetailDrawer desktopId={desktopId} onClose={onClose} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}


describe("DesktopDetailDrawer", () => {
  beforeEach(() => {
    clientMock.getImpl.mockReset();
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders nothing when desktopId is null", () => {
    renderDrawer(null);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("renders identity / status / assignment / history sections when data loads", async () => {
    clientMock.getImpl.mockResolvedValue(makeDetail());
    renderDrawer("d1");
    expect(await screen.findByText("ENG-001")).toBeDefined();
    expect(screen.getByText("Identity")).toBeDefined();
    expect(screen.getByText("Status")).toBeDefined();
    expect(screen.getByText("Assignment")).toBeDefined();
    expect(screen.getByText("History")).toBeDefined();
  });

  it("copy-to-clipboard button copies the desktop ID", async () => {
    clientMock.getImpl.mockResolvedValue(makeDetail());
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
      writable: true,
    });

    renderDrawer("d1");
    await screen.findByText("ENG-001");
    // userEvent.click on a button can interact with navigator.clipboard
    // internally in some happy-dom builds; use fireEvent.click which
    // is purely a synthetic event dispatch.
    fireEvent.click(
      screen.getByRole("button", { name: /copy desktop id/i }),
    );
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith("d1");
    });
  });

  it("copy button writeText is invoked again on subsequent clicks", async () => {
    clientMock.getImpl.mockResolvedValue(makeDetail());
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
      writable: true,
    });

    renderDrawer("d1");
    await screen.findByText("ENG-001");

    const btn = screen.getByRole("button", { name: /copy desktop id/i });
    fireEvent.click(btn);
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledTimes(1);
    });
    fireEvent.click(btn);
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledTimes(2);
    });
  });

  it("ESC closes the drawer", async () => {
    clientMock.getImpl.mockResolvedValue(makeDetail());
    const onClose = vi.fn();
    const user = userEvent.setup();
    renderDrawer("d1", onClose);
    await screen.findByText("ENG-001");
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });

  it("clicking the backdrop closes the drawer", async () => {
    clientMock.getImpl.mockResolvedValue(makeDetail());
    const onClose = vi.fn();
    const user = userEvent.setup();
    const { container } = renderDrawer("d1", onClose);
    await screen.findByText("ENG-001");

    // The backdrop is the first sibling div with the fixed inset-0 class.
    const backdrop = container.querySelector("div.fixed.inset-0");
    expect(backdrop).not.toBeNull();
    await user.click(backdrop!);
    expect(onClose).toHaveBeenCalled();
  });

  it("shows error message if the detail query fails", async () => {
    clientMock.getImpl.mockRejectedValue(new Error("boom"));
    renderDrawer("d1");
    expect(
      await screen.findByText(/Couldn't load desktop details/i),
    ).toBeDefined();
  });

  it("renders the active session section only when active_session is non-null", async () => {
    clientMock.getImpl.mockResolvedValue(
      makeDetail({ active_session: makeSession() }),
    );
    renderDrawer("d1");
    expect(await screen.findByText(/Active session/i)).toBeDefined();
    // Session ID is rendered as a copyable field.
    expect(screen.getByText("s1")).toBeDefined();
  });
});
