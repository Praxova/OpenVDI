import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  DataTable,
  type DataTableColumn,
} from "@/components/admin/DataTable";

interface Row {
  id: string;
  name: string;
  count: number;
}

const COLUMNS: DataTableColumn<Row>[] = [
  { header: "Name", cell: (r) => r.name },
  { header: "Count", cell: (r) => r.count, align: "right" },
];

const ROWS: Row[] = [
  { id: "a", name: "alpha", count: 1 },
  { id: "b", name: "beta", count: 2 },
];

describe("DataTable", () => {
  it("renders headers and rows", () => {
    render(
      <DataTable
        columns={COLUMNS}
        data={ROWS}
        isPending={false}
        error={null}
        rowKey={(r) => r.id}
      />,
    );
    expect(screen.getByText("Name")).toBeDefined();
    expect(screen.getByText("Count")).toBeDefined();
    expect(screen.getByText("alpha")).toBeDefined();
    expect(screen.getByText("beta")).toBeDefined();
  });

  it("shows skeleton when isPending", () => {
    render(
      <DataTable
        columns={COLUMNS}
        data={undefined}
        isPending
        error={null}
        rowKey={(r) => r.id}
      />,
    );
    expect(screen.getByRole("status", { name: /loading/i })).toBeDefined();
    expect(screen.queryByText("alpha")).toBeNull();
  });

  it("shows error + retry button calls onRetry", async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    render(
      <DataTable
        columns={COLUMNS}
        data={undefined}
        isPending={false}
        error={new Error("boom")}
        onRetry={onRetry}
        rowKey={(r) => r.id}
      />,
    );
    expect(screen.getByRole("alert")).toBeDefined();
    await user.click(screen.getByRole("button", { name: /try again/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("shows custom empty message when data is empty", () => {
    render(
      <DataTable
        columns={COLUMNS}
        data={[]}
        isPending={false}
        error={null}
        rowKey={(r) => r.id}
        emptyMessage="No widgets yet."
      />,
    );
    expect(screen.getByText("No widgets yet.")).toBeDefined();
  });

  it("right-aligns columns with align='right'", () => {
    render(
      <DataTable
        columns={COLUMNS}
        data={ROWS}
        isPending={false}
        error={null}
        rowKey={(r) => r.id}
      />,
    );
    const countHeader = screen.getByText("Count");
    expect(countHeader.className).toContain("text-right");
  });
});
