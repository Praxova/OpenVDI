/**
 * Decode the payload of a JWT access token. NO signature verification —
 * the broker validates signatures on every request; the portal just
 * needs the claims for UI gating (admin vs user role) and refresh
 * scheduling (exp).
 *
 * Trusts the token came from our broker. If it didn't, it's not a
 * portal-side concern — the broker rejects bad tokens at the API
 * boundary.
 */
export interface AccessTokenClaims {
  sub: string;                     // canonical lowercase username
  groups: string[];
  role: "admin" | "user";
  iat: number;                     // issued-at unix seconds
  exp: number;                     // expiry unix seconds
  jti: string;                     // auth_tokens row id (UUID string)
}

export function decodeAccessToken(token: string): AccessTokenClaims {
  const parts = token.split(".");
  if (parts.length !== 3) {
    throw new Error(`malformed JWT: expected 3 parts, got ${parts.length}`);
  }
  const payloadPart = parts[1];
  if (payloadPart === undefined || payloadPart === "") {
    throw new Error("malformed JWT: empty payload");
  }
  // base64url → base64 (with padding)
  const padding = "=".repeat((4 - (payloadPart.length % 4)) % 4);
  const base64 = (payloadPart + padding).replace(/-/g, "+").replace(/_/g, "/");
  let json: string;
  try {
    json = atob(base64);
  } catch {
    throw new Error("malformed JWT: payload is not base64");
  }
  let decoded: unknown;
  try {
    decoded = JSON.parse(json);
  } catch {
    throw new Error("malformed JWT: payload is not JSON");
  }
  return decoded as AccessTokenClaims;
}
