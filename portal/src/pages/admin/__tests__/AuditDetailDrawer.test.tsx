import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { AuditDetailDrawer } from "@/pages/admin/AuditDetailDrawer";
import type { AuditRead } from "@/types/admin";


function makeRow(overrides: Partial<AuditRead> = {}): AuditRead {
  return {
    id: 42,
    timestamp: new Date().toISOString(),
    actor: "alice",
    action: "desktop.assign",
    resource_type: "desktop",
    resource_id: "d1234567-89ab-cdef-0123-456789abcdef",
    details: { assigned_to: "alice", assignment_type: "manual" },
    client_ip: "10.0.0.5",
    ...overrides,
  };
}

function renderDrawer(
  row: AuditRead | null,
  onClose: () => void = () => {},
) {
  return render(
    <MemoryRouter>
      <AuditDetailDrawer row={row} onClose={onClose} />
    </MemoryRouter>,
  );
}


describe("AuditDetailDrawer", () => {
  it("renders nothing when row is null", () => {
    renderDrawer(null);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("renders all sections with present data", () => {
    renderDrawer(makeRow());
    expect(screen.getByText("Audit row")).toBeDefined();
    // Section headings are <h3>; "Action" appears twice ("Action"
    // section header AND "Action" field label) so scope the section
    // assertions to heading role.
    expect(screen.getByRole("heading", { level: 3, name: "When" })).toBeDefined();
    expect(screen.getByRole("heading", { level: 3, name: "Action" })).toBeDefined();
    expect(screen.getByRole("heading", { level: 3, name: "Resource" })).toBeDefined();
    expect(screen.getByRole("heading", { level: 3, name: "Details" })).toBeDefined();
    expect(screen.getByText("desktop.assign")).toBeDefined();
    // details JSON pre-block.
    expect(screen.getByText(/"assigned_to": "alice"/)).toBeDefined();
  });

  it("renders 'system' italic when actor is null", () => {
    renderDrawer(makeRow({ actor: null }));
    expect(screen.getByText("system")).toBeDefined();
  });

  it("renders '—' for client_ip when null", () => {
    renderDrawer(makeRow({ client_ip: null, actor: null }));
    // dash appears in the Client IP field.
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(1);
  });

  it("hides Resource section when both resource_type and resource_id are null", () => {
    renderDrawer(
      makeRow({ resource_type: null, resource_id: null }),
    );
    expect(
      screen.queryByRole("heading", { level: 3, name: "Resource" }),
    ).toBeNull();
  });

  it("hides Details section when details is null", () => {
    renderDrawer(makeRow({ details: null }));
    expect(
      screen.queryByRole("heading", { level: 3, name: "Details" }),
    ).toBeNull();
  });

  it("hides Details section when details is empty object {}", () => {
    renderDrawer(makeRow({ details: {} }));
    expect(
      screen.queryByRole("heading", { level: 3, name: "Details" }),
    ).toBeNull();
  });

  it("ESC closes the drawer", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    renderDrawer(makeRow(), onClose);
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });

  it("clicking the backdrop closes the drawer", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    const { container } = renderDrawer(makeRow(), onClose);
    const backdrop = container.querySelector("div.fixed.inset-0");
    expect(backdrop).not.toBeNull();
    await user.click(backdrop!);
    expect(onClose).toHaveBeenCalled();
  });
});
