import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Standalone (not merged into vite.config.ts) so test-only changes
// — environment, setup files, coverage — don't bleed into the dev
// server config. Vitest auto-picks vitest.config.ts over vite.config.ts
// when both exist.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    // No DOM needed for the M3-02 spec; M3-05 will switch this to
    // happy-dom (or override per-spec) for component tests.
    environment: "node",
    // describe/it/expect available without imports.
    globals: true,
    include: ["src/**/*.{test,test-d}.{ts,tsx}"],
    typecheck: {
      enabled: true,
      include: ["src/**/*.test-d.ts"],
    },
  },
});
