import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { FormField } from "@/components/admin/FormField";

describe("FormField", () => {
  it("renders label and child input", () => {
    render(
      <FormField label="Username" htmlFor="user">
        <input id="user" />
      </FormField>,
    );
    const label = screen.getByText("Username");
    expect(label).toBeDefined();
    expect(label.tagName).toBe("LABEL");
    expect(label.getAttribute("for")).toBe("user");
    expect(screen.getByRole("textbox")).toBeDefined();
  });

  it("renders required asterisk when required=true", () => {
    render(
      <FormField label="Username" htmlFor="user" required>
        <input id="user" />
      </FormField>,
    );
    expect(screen.getByText("*")).toBeDefined();
  });

  it("shows hint when no error", () => {
    render(
      <FormField label="Token" htmlFor="t" hint="Paste it here">
        <input id="t" />
      </FormField>,
    );
    expect(screen.getByText("Paste it here")).toBeDefined();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows error and hides hint when error is set", () => {
    render(
      <FormField
        label="Token"
        htmlFor="t"
        hint="Paste it here"
        error="Required"
      >
        <input id="t" />
      </FormField>,
    );
    expect(screen.getByRole("alert")).toBeDefined();
    expect(screen.getByText("Required")).toBeDefined();
    expect(screen.queryByText("Paste it here")).toBeNull();
  });
});
