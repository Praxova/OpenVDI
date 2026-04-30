import { describe, expect, it } from "vitest";
import { decodeAccessToken } from "@/auth/jwt";

// Helper to build a synthetic JWT with arbitrary claims (no signature
// validation needed — decoder ignores signature).
function makeJwt(claims: object): string {
  const header = btoa(JSON.stringify({ alg: "HS256", typ: "JWT" }))
    .replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
  const payload = btoa(JSON.stringify(claims))
    .replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
  return `${header}.${payload}.fakesig`;
}

describe("decodeAccessToken", () => {
  it("returns claims for a valid token", () => {
    const token = makeJwt({
      sub: "alice",
      groups: ["Engineering"],
      role: "user",
      iat: 1234567890,
      exp: 1234568790,
      jti: "00000000-0000-0000-0000-000000000001",
    });
    const claims = decodeAccessToken(token);
    expect(claims.sub).toBe("alice");
    expect(claims.role).toBe("user");
    expect(claims.groups).toEqual(["Engineering"]);
    expect(claims.exp).toBe(1234568790);
  });

  it("throws on malformed JWT (wrong segment count)", () => {
    expect(() => decodeAccessToken("not.a.real.token")).toThrow(/3 parts/);
    expect(() => decodeAccessToken("nope")).toThrow(/3 parts/);
  });

  it("throws on empty payload segment", () => {
    expect(() => decodeAccessToken("a..c")).toThrow(/empty payload/);
  });

  it("throws on non-base64 payload", () => {
    expect(() => decodeAccessToken("a.???.c")).toThrow(/base64/);
  });

  it("throws on non-JSON payload", () => {
    const notJson = btoa("not-json-content");
    expect(() => decodeAccessToken(`a.${notJson}.c`)).toThrow(/JSON/);
  });

  it("decodes admin role", () => {
    const token = makeJwt({
      sub: "admin",
      groups: [],
      role: "admin",
      iat: 0,
      exp: 9999999999,
      jti: "abc",
    });
    expect(decodeAccessToken(token).role).toBe("admin");
  });
});
