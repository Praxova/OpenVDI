import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { LoginError, useAuth } from "@/auth/AuthContext";
import { BrandMark } from "@/components/BrandMark";

interface LocationState {
  from?: string;
}

export function LoginPage() {
  const { state, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const fromState = location.state as LocationState | null;
  const redirectTo =
    fromState?.from && fromState.from !== "/login"
      ? fromState.from
      : "/desktops";

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // If already authenticated, bounce. Covers refreshing /login in an
  // existing session, or opening /login in a second tab.
  if (state.status === "authenticated") {
    return <Navigate to={redirectTo} replace />;
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      await login(username.trim(), password);
      navigate(redirectTo, { replace: true });
    } catch (exc) {
      if (exc instanceof LoginError) {
        if (exc.code === "SERVICE_UNAVAILABLE") {
          setError(
            "The authentication service is temporarily unavailable. " +
              "Try again in a moment.",
          );
        } else {
          setError("Invalid username or password.");
        }
      } else {
        setError("Something went wrong. Try again.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="min-h-screen flex items-center justify-center px-6 bg-bg">
      <form
        onSubmit={handleSubmit}
        aria-labelledby="login-title"
        className={
          "w-full max-w-sm bg-surface-1 border border-border-subtle " +
          "rounded-lg shadow-md p-8 flex flex-col gap-5"
        }
      >
        <div className="flex justify-center">
          <BrandMark size={48} />
        </div>
        <h1
          id="login-title"
          className="font-display text-h2 font-semibold text-text-primary text-center"
        >
          Sign in
        </h1>

        {error !== null && (
          <div
            role="alert"
            className={
              "px-3 py-2 rounded-sm text-body-sm " +
              "bg-danger-bg text-danger-fg border border-danger-border"
            }
          >
            {error}
          </div>
        )}

        <label className="flex flex-col gap-1">
          <span className="text-body-sm font-medium text-text-primary">
            Username
          </span>
          <input
            type="text"
            autoComplete="username"
            required
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className={
              "h-10 px-3 rounded-md bg-bg border border-border-default " +
              "text-text-primary text-body " +
              "transition-colors duration-fast ease-out " +
              "hover:border-border-strong " +
              "focus:outline-none focus:border-border-focus focus:shadow-focus"
            }
          />
        </label>

        <label className="flex flex-col gap-1">
          <span className="text-body-sm font-medium text-text-primary">
            Password
          </span>
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={
              "h-10 px-3 rounded-md bg-bg border border-border-default " +
              "text-text-primary text-body " +
              "transition-colors duration-fast ease-out " +
              "hover:border-border-strong " +
              "focus:outline-none focus:border-border-focus focus:shadow-focus"
            }
          />
        </label>

        <button
          type="submit"
          disabled={
            submitting || username.trim() === "" || password === ""
          }
          className={
            "h-10 px-4 rounded-md " +
            "bg-action-primary text-text-on-accent text-body font-medium " +
            "transition-colors duration-fast ease-out " +
            "hover:bg-action-primary-hover " +
            "active:bg-action-primary-active " +
            "focus-visible:outline-none focus-visible:shadow-focus " +
            "disabled:opacity-50 disabled:cursor-not-allowed"
          }
        >
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
