import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { EntitlementsPanel } from "@/pages/admin/EntitlementsPanel";
import { BrokerError } from "@/api/errors";
import type { EntitlementRead } from "@/types/admin";


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


function makeEnt(
  id: string,
  type: "user" | "group",
  name: string,
): EntitlementRead {
  return {
    id,
    pool_id: "p1",
    principal_type: type,
    principal_name: name,
    created_at: new Date().toISOString(),
  };
}

function setupList(value: EntitlementRead[] | Error) {
  clientMock.getImpl.mockImplementation(async (path: string) => {
    if (path.includes("/entitlements")) {
      if (value instanceof Error) throw value;
      return value;
    }
    throw new Error(`unexpected: ${path}`);
  });
}

function renderPanel() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <EntitlementsPanel poolId="p1" />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("EntitlementsPanel", () => {
  beforeEach(() => {
    Object.values(clientMock).forEach((m) => m.mockReset());
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders existing entitlements list", async () => {
    setupList([makeEnt("e1", "group", "VDI-Engineering")]);
    renderPanel();
    expect(await screen.findByText("VDI-Engineering")).toBeDefined();
    expect(screen.getByText("group")).toBeDefined();
  });

  it("renders empty state when no entitlements", async () => {
    setupList([]);
    renderPanel();
    expect(
      await screen.findByText(/No entitlements yet/i),
    ).toBeDefined();
  });

  it("renders error state on list query failure", async () => {
    setupList(new Error("boom"));
    renderPanel();
    expect(
      await screen.findByText(/Couldn't load entitlements/i),
    ).toBeDefined();
  });

  it("add submits EntitlementCreate", async () => {
    setupList([]);
    clientMock.postImpl.mockResolvedValue(makeEnt("e1", "group", "VDI-X"));
    const user = userEvent.setup();
    renderPanel();

    await screen.findByText(/No entitlements yet/i);
    await user.type(screen.getByLabelText(/group name/i), "VDI-X");
    await user.click(screen.getByRole("button", { name: /^add$/i }));

    await waitFor(() => {
      expect(clientMock.postImpl).toHaveBeenCalledWith(
        "/api/v1/pools/p1/entitlements",
        { principal_type: "group", principal_name: "VDI-X" },
      );
    });
  });

  it("add success clears the name input", async () => {
    setupList([]);
    clientMock.postImpl.mockResolvedValue(makeEnt("e1", "group", "VDI-X"));
    const user = userEvent.setup();
    renderPanel();

    await screen.findByText(/No entitlements yet/i);
    const input = screen.getByLabelText(/group name/i) as HTMLInputElement;
    await user.type(input, "VDI-X");
    await user.click(screen.getByRole("button", { name: /^add$/i }));
    await waitFor(() => {
      expect(input.value).toBe("");
    });
  });

  it("add 409 shows 'already entitled' inline error", async () => {
    setupList([]);
    clientMock.postImpl.mockRejectedValue(
      new BrokerError({
        httpStatus: 409,
        code: "CONFLICT",
        message: "duplicate",
        envelope: null,
      }),
    );
    const user = userEvent.setup();
    renderPanel();

    await screen.findByText(/No entitlements yet/i);
    await user.type(screen.getByLabelText(/group name/i), "VDI-X");
    await user.click(screen.getByRole("button", { name: /^add$/i }));

    expect(
      await screen.findByText(/already entitled/i),
    ).toBeDefined();
  });

  it("user-type entitlement coerces to lowercase on submit", async () => {
    setupList([]);
    clientMock.postImpl.mockResolvedValue(makeEnt("e1", "user", "alice"));
    const user = userEvent.setup();
    renderPanel();

    await screen.findByText(/No entitlements yet/i);
    await user.selectOptions(screen.getByLabelText(/^type$/i), "user");
    await user.type(screen.getByLabelText(/username/i), "ALICE");
    await user.click(screen.getByRole("button", { name: /^add$/i }));

    await waitFor(() => {
      expect(clientMock.postImpl).toHaveBeenCalledWith(
        "/api/v1/pools/p1/entitlements",
        { principal_type: "user", principal_name: "alice" },
      );
    });
  });

  it("revoke confirms via window.confirm and invokes delete", async () => {
    setupList([makeEnt("e1", "group", "VDI-Engineering")]);
    clientMock.deleteImpl.mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderPanel();

    await screen.findByText("VDI-Engineering");
    await user.click(
      screen.getByRole("button", { name: /revoke vdi-engineering/i }),
    );
    await waitFor(() => {
      expect(clientMock.deleteImpl).toHaveBeenCalledWith(
        "/api/v1/pools/p1/entitlements/e1",
      );
    });
  });

  it("revoke is canceled when user clicks 'Cancel' in confirm", async () => {
    setupList([makeEnt("e1", "group", "VDI-Engineering")]);
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    renderPanel();

    await screen.findByText("VDI-Engineering");
    await user.click(
      screen.getByRole("button", { name: /revoke vdi-engineering/i }),
    );
    expect(clientMock.deleteImpl).not.toHaveBeenCalled();
  });
});
