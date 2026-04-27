import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import type { AuthState, DevUser } from "./types";
import { clearUser, readUser, writeUser } from "./storage";

/**
 * Authentication context for the M3 dev-auth flow.
 *
 * The default value is null so any `useAuth()` call outside the
 * provider throws — early loud failure beats silent surprise. M3-03
 * mounts `<AuthProvider>` in `main.tsx` so this only fires in tests
 * that forget the wrapper.
 */
const AuthContext = createContext<AuthState | null>(null);

interface AuthProviderProps {
  children: ReactNode;
}

export function AuthProvider({ children }: AuthProviderProps) {
  // Lazy initializer — readUser() runs once, on mount. This is the
  // "rehydrate from localStorage on page reload" path.
  const [currentUser, setCurrentUser] = useState<DevUser | null>(() => readUser());

  const login = useCallback((user: DevUser) => {
    writeUser(user);
    setCurrentUser(user);
  }, []);

  const logout = useCallback(() => {
    clearUser();
    setCurrentUser(null);
  }, []);

  const value = useMemo<AuthState>(
    () => ({ currentUser, login, logout }),
    [currentUser, login, logout],
  );

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (ctx === null) {
    throw new Error(
      "useAuth called outside an <AuthProvider>. " +
        "M3-03 mounts the provider in main.tsx; " +
        "if a test triggers this, wrap the render in <AuthProvider>.",
    );
  }
  return ctx;
}
