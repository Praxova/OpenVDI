import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { AuditPage } from "@/pages/admin";
import type { AuditRead } from "@/types/admin";


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


function makeRow(
  id: number,
  overrides: Partial<AuditRead> = {},
): AuditRead {
  return {
    id,
    timestamp: new Date().toISOString(),
    actor: "alice",
    action: "desktop.assign",
    resource_type: "desktop",
    resource_id: "d1234567-89ab-cdef-0123-456789abcdef",
    details: null,
    client_ip: "10.0.0.5",
    ...overrides,
  };
}

function setupGet(rows: AuditRead[] | Error) {
  clientMock.getImpl.mockImplementation(async () => {
    if (rows instanceof Error) throw rows;
    return rows;
  });
}

function lastQueryUrl(): string {
  const calls = clientMock.getImpl.mock.calls.map((c) => c[0] as string);
  const last = calls[calls.length - 1];
  if (last === undefined) throw new Error("no fetch call recorded");
  return last;
}

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <MemoryRouter initialEntries={["/admin/audit"]}>
      <QueryClientProvider client={qc}>
        <AuditPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}


describe("AuditPage", () => {
  beforeEach(() => {
    clientMock.getImpl.mockReset();
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders rows in newest-first order returned by the broker", async () => {
    setupGet([
      makeRow(101, { actor: "alice", action: "desktop.assign" }),
      makeRow(100, { actor: "bob", action: "pool.create" }),
    ]);
    renderPage();
    expect(await screen.findByText("desktop.assign")).toBeDefined();
    expect(screen.getByText("pool.create")).toBeDefined();
  });

  it("renders empty state when no rows", async () => {
    setupGet([]);
    renderPage();
    expect(
      await screen.findByText(/No audit rows match/i),
    ).toBeDefined();
  });

  it("renders error state with retry", async () => {
    setupGet(new Error("boom"));
    renderPage();
    expect(
      await screen.findByText(/Couldn't load the list/i),
    ).toBeDefined();
  });

  it("actor filter passes actor query param", async () => {
    setupGet([makeRow(1)]);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("desktop.assign");
    await user.type(screen.getByLabelText(/^actor$/i), "alice");
    await waitFor(() => {
      expect(lastQueryUrl()).toContain("actor=alice");
    });
  });

  it("action filter literal sends action param verbatim", async () => {
    setupGet([makeRow(1)]);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("desktop.assign");
    await user.type(
      screen.getByLabelText(/^action$/i),
      "pool.create",
    );
    await waitFor(() => {
      expect(lastQueryUrl()).toContain("action=pool.create");
    });
  });

  it("action filter with trailing * sends pool.* prefix", async () => {
    setupGet([makeRow(1)]);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("desktop.assign");
    await user.type(screen.getByLabelText(/^action$/i), "pool.*");
    await waitFor(() => {
      // URLSearchParams encodes * as %2A.
      expect(lastQueryUrl()).toMatch(/action=pool\.(\*|%2A)/);
    });
  });

  it("resource_type dropdown narrows by type", async () => {
    setupGet([makeRow(1)]);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("desktop.assign");
    await user.selectOptions(
      screen.getByLabelText(/resource type/i),
      "pool",
    );
    await waitFor(() => {
      expect(lastQueryUrl()).toContain("resource_type=pool");
    });
  });

  it("resource_id with valid UUID flows into the query; invalid is omitted", async () => {
    setupGet([makeRow(1)]);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("desktop.assign");

    const input = screen.getByLabelText(/resource id/i) as HTMLInputElement;

    // Invalid UUID: aria-invalid set, NOT sent in query.
    await user.type(input, "abc");
    await waitFor(() => {
      expect(input.getAttribute("aria-invalid")).toBe("true");
      expect(lastQueryUrl()).not.toContain("resource_id=");
    });

    // Valid UUID: aria-invalid clears, query includes resource_id.
    const valid = "12345678-1234-1234-1234-123456789abc";
    await user.clear(input);
    await user.type(input, valid);
    await waitFor(() => {
      expect(input.getAttribute("aria-invalid")).toBe("false");
      expect(lastQueryUrl()).toContain(`resource_id=${valid}`);
    });
  });

  it("time-range preset sends since param", async () => {
    setupGet([makeRow(1)]);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("desktop.assign");
    await user.selectOptions(screen.getByLabelText(/time range/i), "7d");
    await waitFor(() => {
      expect(lastQueryUrl()).toContain("since=");
    });
  });

  it("clicking actor in a row populates the actor filter", async () => {
    setupGet([makeRow(1, { actor: "carol" })]);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("desktop.assign");
    await user.click(screen.getByRole("button", { name: "carol" }));
    const input = screen.getByLabelText(/^actor$/i) as HTMLInputElement;
    await waitFor(() => {
      expect(input.value).toBe("carol");
    });
  });

  it("clicking action in a row populates the action filter", async () => {
    setupGet([makeRow(1, { action: "session.end" })]);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("session.end");
    await user.click(screen.getByRole("button", { name: "session.end" }));
    const input = screen.getByLabelText(/^action$/i) as HTMLInputElement;
    await waitFor(() => {
      expect(input.value).toBe("session.end");
    });
  });

  it("Prev disabled at offset=0; Next disabled when fewer than LIMIT rows", async () => {
    setupGet([makeRow(1)]);
    renderPage();
    await screen.findByText("desktop.assign");
    const prev = screen.getByRole("button", {
      name: /previous page/i,
    }) as HTMLButtonElement;
    const next = screen.getByRole("button", {
      name: /next page/i,
    }) as HTMLButtonElement;
    expect(prev.disabled).toBe(true);
    expect(next.disabled).toBe(true); // only 1 row < 50
  });

  it("Next advances offset; filter change resets offset to 0", async () => {
    // First fetch returns 50 rows so Next becomes enabled.
    const fullPage = Array.from({ length: 50 }, (_, i) =>
      makeRow(1000 - i, { action: `act.${i}` }),
    );
    clientMock.getImpl.mockImplementation(async (path: string) => {
      if (path.includes("offset=0")) return fullPage;
      // Subsequent pages: smaller chunk (< LIMIT) so Next disables.
      return [makeRow(900)];
    });
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("act.0");
    const next = screen.getByRole("button", { name: /next page/i });
    await user.click(next);
    await waitFor(() => {
      expect(lastQueryUrl()).toContain("offset=50");
    });

    // Filter change resets offset to 0.
    await user.type(screen.getByLabelText(/^actor$/i), "alice");
    await waitFor(() => {
      const url = lastQueryUrl();
      expect(url).toContain("offset=0");
      expect(url).toContain("actor=alice");
    });
  });
});
