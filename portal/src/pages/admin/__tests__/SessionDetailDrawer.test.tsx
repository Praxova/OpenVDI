import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { SessionDetailDrawer } from "@/pages/admin/SessionDetailDrawer";
import type { SessionReadDetailed } from "@/types/admin";


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


function makeDetail(
  overrides: Partial<SessionReadDetailed> = {},
): SessionReadDetailed {
  return {
    id: "s1",
    desktop_id: "d1",
    desktop_name: "ENG-001",
    pool_id: "p1",
    pool_name: "Engineering",
    pool_type: "nonpersistent",
    username: "alice",
    protocol: "novnc",
    client_ip: "10.0.0.5",
    status: "active",
    connected_at: new Date().toISOString(),
    disconnected_at: null,
    ended_at: null,
    last_heartbeat: new Date().toISOString(),
    created_at: new Date().toISOString(),
    os_user: null,
    os_info: null,
    vm_ip_address: null,
    idle_since: null,
    ...overrides,
  };
}

function renderDrawer(
  sessionId: string | null,
  onClose: () => void = () => {},
) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <SessionDetailDrawer sessionId={sessionId} onClose={onClose} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}


describe("SessionDetailDrawer", () => {
  beforeEach(() => {
    clientMock.getImpl.mockReset();
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders nothing when sessionId is null", () => {
    renderDrawer(null);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("renders Identity / Status / Times sections when data loads", async () => {
    clientMock.getImpl.mockResolvedValue(makeDetail());
    renderDrawer("s1");
    expect(await screen.findByText("alice")).toBeDefined();
    expect(screen.getByText("Identity")).toBeDefined();
    expect(screen.getByText("Status")).toBeDefined();
    expect(screen.getByText("Times")).toBeDefined();
  });

  it("renders '(deleted)' inline for orphaned desktop_id / pool_id", async () => {
    clientMock.getImpl.mockResolvedValue(
      makeDetail({ desktop_id: null, pool_id: null }),
    );
    renderDrawer("s1");
    await screen.findByText("alice");
    expect(screen.getAllByText(/\(deleted\)/i).length).toBeGreaterThanOrEqual(2);
  });

  it("hides Guest telemetry section when all telemetry fields are null", async () => {
    clientMock.getImpl.mockResolvedValue(makeDetail());
    renderDrawer("s1");
    await screen.findByText("alice");
    expect(screen.queryByText(/Guest telemetry/i)).toBeNull();
  });

  it("renders Guest telemetry section when any telemetry field is populated", async () => {
    clientMock.getImpl.mockResolvedValue(
      makeDetail({
        os_user: "ALICE",
        vm_ip_address: "10.0.0.10",
        os_info: { name: "Ubuntu", version: "24.04" },
      }),
    );
    renderDrawer("s1");
    await screen.findByText("alice");
    expect(screen.getByText(/Guest telemetry/i)).toBeDefined();
    expect(screen.getByText("ALICE")).toBeDefined();
    expect(screen.getByText("10.0.0.10")).toBeDefined();
    // os_info renders as a JSON pre-block.
    expect(screen.getByText(/"name": "Ubuntu"/)).toBeDefined();
  });

  it("copy button copies the session ID", async () => {
    clientMock.getImpl.mockResolvedValue(makeDetail());
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
      writable: true,
    });

    renderDrawer("s1");
    await screen.findByText("alice");
    fireEvent.click(
      screen.getByRole("button", { name: /copy session id/i }),
    );
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith("s1");
    });
  });

  it("ESC closes the drawer", async () => {
    clientMock.getImpl.mockResolvedValue(makeDetail());
    const onClose = vi.fn();
    const user = userEvent.setup();
    renderDrawer("s1", onClose);
    await screen.findByText("alice");
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });

  it("clicking the backdrop closes the drawer", async () => {
    clientMock.getImpl.mockResolvedValue(makeDetail());
    const onClose = vi.fn();
    const user = userEvent.setup();
    const { container } = renderDrawer("s1", onClose);
    await screen.findByText("alice");
    const backdrop = container.querySelector("div.fixed.inset-0");
    expect(backdrop).not.toBeNull();
    await user.click(backdrop!);
    expect(onClose).toHaveBeenCalled();
  });
});
