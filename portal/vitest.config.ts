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
    // @novnc/novnc@1.6 ships only a `browser` entry in its package.json
    // (no `main`/`module`/`exports`). Vitest's default SSR-style
    // resolver doesn't honor `browser`, so the import fails to resolve
    // even though tests mock the module. Including "browser" first
    // matches what the production Vite build does.
    mainFields: ["browser", "module", "main"],
  },
  test: {
    // happy-dom (since M3-03): the AuthContext hook test exercises
    // localStorage + React render, the theme module asserts on
    // document.documentElement, and M3-05's component tests will need
    // a DOM too. The M3-02 client tests continue to pass under
    // happy-dom — Response and fetch are present in both environments.
    environment: "happy-dom",
    // describe/it/expect available without imports.
    globals: true,
    include: ["src/**/*.{test,test-d}.{ts,tsx}"],
    typecheck: {
      enabled: true,
      include: ["src/**/*.test-d.ts"],
    },
  },
});
