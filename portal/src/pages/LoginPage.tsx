import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "@/auth/AuthContext";
import { BrandMark } from "@/components/BrandMark";
import type { Role } from "@/auth/types";

interface LocationState {
  from?: string;
}

export function LoginPage() {
  const { currentUser, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  // If we're already logged in, bounce. Covers refreshing the login
  // page in an existing session.
  if (currentUser !== null) {
    return <Navigate to="/desktops" replace />;
  }

  const fromState = location.state as LocationState | null;
  const redirectTo =
    fromState?.from && fromState.from !== "/login"
      ? fromState.from
      : "/desktops";

  return (
    <LoginForm
      onLogin={(user) => {
        login(user);
        navigate(redirectTo, { replace: true });
      }}
    />
  );
}

interface LoginFormProps {
  onLogin: (user: { username: string; groups: string[]; role: Role }) => void;
}

function LoginForm({ onLogin }: LoginFormProps) {
  const [username, setUsername] = useState("");
  const [groupsRaw, setGroupsRaw] = useState("");
  const [role, setRole] = useState<Role>("user");
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    if (username.trim() === "") return;

    const groups = groupsRaw
      .split(",")
      .map((g) => g.trim())
      .filter((g) => g.length > 0);

    onLogin({ username: username.trim(), groups, role });
  };

  const usernameError =
    submitted && username.trim() === "" ? "Username is required" : null;

  return (
    <main className="min-h-screen flex items-center justify-center p-6 bg-bg">
      <section
        aria-labelledby="login-title"
        className={
          "w-full max-w-md " +
          "bg-surface-1 border border-border-subtle rounded-lg shadow-md " +
          "p-6"
        }
      >
        <header className="flex flex-col items-center gap-3 mb-6">
          <BrandMark size={48} />
          <h1
            id="login-title"
            className="font-display text-h2 font-semibold text-text-primary"
          >
            OpenVDI
          </h1>
          <p className="text-body-sm text-text-secondary text-center">
            M3 dev-auth — header-based identity for local development.
            Production LDAP sign-in arrives in Milestone 4.
          </p>
        </header>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
          <div>
            <label
              htmlFor="username"
              className="block text-body-sm font-medium text-text-primary mb-1.5"
            >
              Username
              <span className="text-action-primary ml-0.5" aria-hidden>
                {" *"}
              </span>
            </label>
            <input
              id="username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoFocus
              aria-invalid={usernameError !== null}
              aria-describedby={
                usernameError !== null ? "username-error" : undefined
              }
              className={
                "w-full h-10 px-3 py-2 rounded-md " +
                "bg-surface-1 text-text-primary text-body " +
                "border " +
                (usernameError !== null
                  ? "border-danger-solid "
                  : "border-border-default ") +
                "transition-colors duration-fast ease-out " +
                "hover:border-border-strong " +
                "focus:outline-none focus:border-border-focus focus:shadow-focus"
              }
            />
            {usernameError !== null && (
              <p
                id="username-error"
                className="text-caption text-danger-fg mt-1.5"
              >
                {usernameError}
              </p>
            )}
          </div>

          <div>
            <label
              htmlFor="groups"
              className="block text-body-sm font-medium text-text-primary mb-1.5"
            >
              Groups
            </label>
            <input
              id="groups"
              type="text"
              value={groupsRaw}
              onChange={(e) => setGroupsRaw(e.target.value)}
              placeholder="engineering-all, vpn-users"
              aria-describedby="groups-help"
              className={
                "w-full h-10 px-3 py-2 rounded-md " +
                "bg-surface-1 text-text-primary text-body " +
                "border border-border-default " +
                "transition-colors duration-fast ease-out " +
                "hover:border-border-strong " +
                "focus:outline-none focus:border-border-focus focus:shadow-focus"
              }
            />
            <p
              id="groups-help"
              className="text-caption text-text-tertiary mt-1.5"
            >
              Comma-separated AD-style group names. Leave empty if none.
            </p>
          </div>

          <fieldset>
            <legend className="text-body-sm font-medium text-text-primary mb-1.5">
              Role
            </legend>
            <div
              role="radiogroup"
              className="inline-flex items-center gap-1 p-1 rounded-md bg-surface-2"
            >
              {(["user", "admin"] as const).map((r) => (
                <label
                  key={r}
                  className={
                    "px-3 py-1.5 rounded-sm text-body-sm font-medium cursor-pointer " +
                    "transition-colors duration-fast ease-out " +
                    (role === r
                      ? "bg-bg text-text-primary shadow-sm"
                      : "text-text-secondary hover:text-text-primary")
                  }
                >
                  <input
                    type="radio"
                    name="role"
                    value={r}
                    checked={role === r}
                    onChange={() => setRole(r)}
                    className="sr-only"
                  />
                  {r === "user" ? "User" : "Admin"}
                </label>
              ))}
            </div>
          </fieldset>

          <button
            type="submit"
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
            Sign in
          </button>
        </form>
      </section>
    </main>
  );
}
