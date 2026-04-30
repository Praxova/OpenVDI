import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { decodeAccessToken } from "./jwt";
import type { AuthContextValue, AuthState, User } from "./types";

interface AuthProviderProps {
  children: ReactNode;
}

interface TokenResponseBody {
  access_token: string;
  expires_in: number;
  role: "admin" | "user";
}

interface ErrorEnvelope {
  code?: string;
  message?: string;
}

const REFRESH_LEAD_MS = 60_000; // refresh ~60s before expiry

const AuthContext = createContext<AuthContextValue | null>(null);

export class LoginError extends Error {
  constructor(public readonly code: string, message: string) {
    super(message);
    Object.setPrototypeOf(this, LoginError.prototype);
    this.name = "LoginError";
  }
}

export function AuthProvider({ children }: AuthProviderProps) {
  const [state, setState] = useState<AuthState>({ status: "initializing" });

  // Refresh-promise de-dup. Multiple callers during a parallel-401
  // burst share one in-flight request — without this, N concurrent
  // 401s fire N /auth/refresh POSTs against the same cookie; the
  // broker rotates on the first call, the rest 401 and force-logout.
  const inFlightRefreshRef = useRef<Promise<string | null> | null>(null);

  // Stable getter for BrokerClient. Reads the latest state without
  // re-creating the closure when state changes (otherwise every state
  // change would re-create the BrokerClient and invalidate every
  // TanStack Query's captured queryFn reference).
  const stateRef = useRef<AuthState>(state);
  stateRef.current = state;

  const getAccessToken = useCallback((): string | null => {
    const s = stateRef.current;
    return s.status === "authenticated" ? s.accessToken : null;
  }, []);

  // ── Login ──────────────────────────────────────────────────

  const login = useCallback(async (username: string, password: string) => {
    const response = await fetch("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include", // accepts the Set-Cookie response
      body: JSON.stringify({ username, password }),
    });
    if (!response.ok) {
      const body = (await response.json().catch(() => null)) as
        | { error?: ErrorEnvelope }
        | null;
      const code = body?.error?.code ?? "UNAUTHORIZED";
      const message = body?.error?.message ?? "invalid credentials";
      throw new LoginError(code, message);
    }
    const envelope = (await response.json()) as { data: TokenResponseBody };
    setState(buildAuthenticatedState(envelope.data));
  }, []);

  // ── Refresh ────────────────────────────────────────────────

  const doRefresh = useCallback(async (): Promise<string | null> => {
    try {
      const response = await fetch("/api/v1/auth/refresh", {
        method: "POST",
        credentials: "include",
      });
      if (!response.ok) {
        setState({ status: "unauthenticated" });
        return null;
      }
      const envelope = (await response.json()) as { data: TokenResponseBody };
      const newState = buildAuthenticatedState(envelope.data);
      setState(newState);
      return newState.status === "authenticated"
        ? newState.accessToken
        : null;
    } catch {
      // Network error etc. — treat as refresh failure.
      setState({ status: "unauthenticated" });
      return null;
    }
  }, []);

  const refresh = useCallback((): Promise<string | null> => {
    if (inFlightRefreshRef.current !== null) {
      return inFlightRefreshRef.current;
    }
    const promise = doRefresh();
    inFlightRefreshRef.current = promise;
    void promise.finally(() => {
      inFlightRefreshRef.current = null;
    });
    return promise;
  }, [doRefresh]);

  // ── Logout ─────────────────────────────────────────────────

  const logout = useCallback(async () => {
    try {
      await fetch("/api/v1/auth/logout", {
        method: "POST",
        credentials: "include",
      });
    } catch {
      // Ignore network failure; the local clear is the user-visible
      // success criterion. Server-side revocation is best-effort —
      // if the cookie was already cleared, the row times out.
    }
    setState({ status: "unauthenticated" });
  }, []);

  // ── Initial refresh on mount ───────────────────────────────

  // Try a refresh against the cookie on app load. If the cookie is
  // valid, the user is back in within ~50ms; if not, refresh fails
  // and ProtectedRoute bounces to /login.
  useEffect(() => {
    void refresh();
    // Empty deps — runs once on mount only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Pre-emptive refresh scheduler ──────────────────────────

  useEffect(() => {
    if (state.status !== "authenticated") return;
    const remainingMs = state.expiresAt - Date.now();
    const refreshIn = Math.max(0, remainingMs - REFRESH_LEAD_MS);
    const timer = setTimeout(() => {
      void refresh();
    }, refreshIn);
    return () => clearTimeout(timer);
  }, [state, refresh]);

  // ── Context value ──────────────────────────────────────────

  const value = useMemo<AuthContextValue>(
    () => ({ state, getAccessToken, login, refresh, logout }),
    [state, getAccessToken, login, refresh, logout],
  );

  return (
    <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (ctx === null) {
    throw new Error(
      "useAuth called outside an <AuthProvider>. " +
        "main.tsx mounts the provider; if a test triggers this, " +
        "wrap the render in <AuthProvider>.",
    );
  }
  return ctx;
}

function buildAuthenticatedState(body: TokenResponseBody): AuthState {
  const claims = decodeAccessToken(body.access_token);
  const user: User = {
    username: claims.sub,
    groups: claims.groups,
    role: claims.role,
  };
  return {
    status: "authenticated",
    user,
    accessToken: body.access_token,
    expiresAt: Date.now() + body.expires_in * 1000,
  };
}
