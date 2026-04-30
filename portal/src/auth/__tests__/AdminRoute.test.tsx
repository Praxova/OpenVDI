import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { AdminRoute } from "@/auth/AdminRoute";
import type { AuthState } from "@/auth/types";

// Mock useAuth — the real AuthProvider does network calls on mount.
// vi.hoisted lets us share the mutable state ref between the mock
// factory (hoisted to the top of the file) and the test bodies.
const authMock = vi.hoisted(() => ({
  state: { status: "unauthenticated" } as AuthState,
}));

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    state: authMock.state,
    getAccessToken: () => null,
    login: vi.fn(),
    refresh: vi.fn(),
    logout: vi.fn(),
  }),
}));

function renderTree(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route element={<AdminRoute />}>
          <Route path="/admin" element={<div>ADMIN_OK</div>} />
        </Route>
        <Route path="/desktops" element={<div>DESKTOPS</div>} />
        <Route path="/login" element={<div>LOGIN</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("AdminRoute", () => {
  afterEach(() => {
    authMock.state = { status: "unauthenticated" };
  });

  it("renders the outlet for authenticated admin", () => {
    authMock.state = {
      status: "authenticated",
      user: { username: "admin", groups: ["Admins"], role: "admin" },
      accessToken: "x",
      expiresAt: Date.now() + 60_000,
    };
    renderTree("/admin");
    expect(screen.getByText("ADMIN_OK")).toBeDefined();
  });

  it("redirects to /desktops for authenticated user (non-admin)", () => {
    authMock.state = {
      status: "authenticated",
      user: { username: "alice", groups: [], role: "user" },
      accessToken: "x",
      expiresAt: Date.now() + 60_000,
    };
    renderTree("/admin");
    expect(screen.queryByText("ADMIN_OK")).toBeNull();
    expect(screen.getByText("DESKTOPS")).toBeDefined();
  });

  it("redirects to /login for unauthenticated state (defensive)", () => {
    authMock.state = { status: "unauthenticated" };
    renderTree("/admin");
    expect(screen.queryByText("ADMIN_OK")).toBeNull();
    expect(screen.getByText("LOGIN")).toBeDefined();
  });

  it("redirects to /login for initializing state (defensive)", () => {
    authMock.state = { status: "initializing" };
    renderTree("/admin");
    expect(screen.queryByText("ADMIN_OK")).toBeNull();
    expect(screen.getByText("LOGIN")).toBeDefined();
  });
});
