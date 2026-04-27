import type { DevUser } from "./types";

const STORAGE_KEY = "openvdi.auth.user";

/**
 * Read the persisted user, returning null if absent or malformed.
 *
 * Malformed entries are treated as null and silently cleared so a
 * future write doesn't compound bad data. Validation is structural
 * — if the shape doesn't match, drop it and start fresh. Lets users
 * recover from a portal version bump without a hard-crash on an
 * undefined-field access.
 */
export function readUser(): DevUser | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === null) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!isDevUser(parsed)) {
      window.localStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function writeUser(user: DevUser): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(user));
  } catch {
    // Same posture as theme.ts: log nothing, fail open. The auth
    // state still lives in the AuthContext for the current tab.
  }
}

export function clearUser(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}

function isDevUser(v: unknown): v is DevUser {
  if (typeof v !== "object" || v === null) return false;
  const r = v as Record<string, unknown>;
  if (typeof r.username !== "string" || r.username.length === 0) return false;
  if (!Array.isArray(r.groups)) return false;
  if (!r.groups.every((g) => typeof g === "string")) return false;
  if (r.role !== "admin" && r.role !== "user") return false;
  return true;
}
