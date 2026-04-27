import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";

import { AuthProvider, useAuth } from "@/auth/AuthContext";

const STORAGE_KEY = "openvdi.auth.user";

function wrapper({ children }: { children: ReactNode }) {
  return <AuthProvider>{children}</AuthProvider>;
}

describe("AuthContext", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    window.localStorage.clear();
  });

  it("starts logged out when localStorage is empty", () => {
    const { result } = renderHook(() => useAuth(), { wrapper });
    expect(result.current.currentUser).toBeNull();
  });

  it("rehydrates from localStorage on mount", () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        username: "alice",
        groups: ["engineering-all"],
        role: "user",
      }),
    );
    const { result } = renderHook(() => useAuth(), { wrapper });
    expect(result.current.currentUser).toEqual({
      username: "alice",
      groups: ["engineering-all"],
      role: "user",
    });
  });

  it("persists on login and clears on logout", () => {
    const { result } = renderHook(() => useAuth(), { wrapper });
    act(() => {
      result.current.login({ username: "bob", groups: [], role: "admin" });
    });
    expect(result.current.currentUser).toEqual({
      username: "bob",
      groups: [],
      role: "admin",
    });
    expect(window.localStorage.getItem(STORAGE_KEY)).toContain("bob");

    act(() => {
      result.current.logout();
    });
    expect(result.current.currentUser).toBeNull();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("drops malformed localStorage entries", () => {
    window.localStorage.setItem(STORAGE_KEY, "{not valid json");
    const { result } = renderHook(() => useAuth(), { wrapper });
    expect(result.current.currentUser).toBeNull();

    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ username: 42 }));
    const { result: result2 } = renderHook(() => useAuth(), { wrapper });
    expect(result2.current.currentUser).toBeNull();
  });

  it("throws when useAuth is called outside a provider", () => {
    // Suppress React's expected error log for this assertion.
    const consoleError = console.error;
    console.error = () => {};
    try {
      expect(() => renderHook(() => useAuth())).toThrow(
        /useAuth called outside an <AuthProvider>/,
      );
    } finally {
      console.error = consoleError;
    }
  });
});
