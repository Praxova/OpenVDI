import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { AdminMenu } from "@/components/AdminMenu";

function renderMenu() {
  return render(
    <MemoryRouter initialEntries={["/desktops"]}>
      <AdminMenu />
    </MemoryRouter>,
  );
}

describe("AdminMenu", () => {
  it("renders the trigger button closed by default", () => {
    renderMenu();
    expect(
      screen.getByRole("button", { name: /admin/i }),
    ).toBeDefined();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("opens the menu on button click", async () => {
    const user = userEvent.setup();
    renderMenu();
    await user.click(screen.getByRole("button", { name: /admin/i }));
    expect(screen.getByRole("menu")).toBeDefined();
    // 7 admin destinations.
    expect(screen.getAllByRole("menuitem")).toHaveLength(7);
    expect(screen.getByText("Dashboard")).toBeDefined();
    expect(screen.getByText("Clusters")).toBeDefined();
    expect(screen.getByText("Templates")).toBeDefined();
    expect(screen.getByText("Pools")).toBeDefined();
    expect(screen.getByText("Audit Log")).toBeDefined();
  });

  it("closes the menu on Escape", async () => {
    const user = userEvent.setup();
    renderMenu();
    const trigger = screen.getByRole("button", { name: /admin/i });
    await user.click(trigger);
    expect(screen.getByRole("menu")).toBeDefined();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("closes the menu on outside click", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/desktops"]}>
        <AdminMenu />
        <div data-testid="outside">outside</div>
      </MemoryRouter>,
    );
    await user.click(screen.getByRole("button", { name: /admin/i }));
    expect(screen.getByRole("menu")).toBeDefined();
    await user.click(screen.getByTestId("outside"));
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("closes the menu when a menu item is clicked", async () => {
    const user = userEvent.setup();
    renderMenu();
    await user.click(screen.getByRole("button", { name: /admin/i }));
    await user.click(screen.getByText("Clusters"));
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("toggles aria-expanded on the trigger button", async () => {
    const user = userEvent.setup();
    renderMenu();
    const trigger = screen.getByRole("button", { name: /admin/i });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    await user.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    await user.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });
});
