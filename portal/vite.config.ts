import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    // @novnc/novnc 1.6 uses top-level await in its WebCodecs feature
    // detection (lib/util/browser.js). Vite's default target
    // ("modules") covers chrome87+/firefox78+/safari14+ which predate
    // TLA support, so the build aborts. Bump the floor to chrome89+/
    // firefox89+/safari15+/edge89+ — anything that can actually run
    // noVNC over wss is well past these versions anyway.
    target: ["chrome89", "edge89", "firefox89", "safari15"],
  },
  server: {
    port: 5173,
    proxy: {
      // HTTP only (ST13). The broker /api/v1/* surface forwards to
      // localhost:8080. The noVNC websocket from M3-06 is
      // browser-direct (wss://{pve-node}) and intentionally NOT proxied
      // — adding ws: true here would cause Vite to intercept WebSocket
      // upgrades on /api which is a footgun for any future maintainer
      // who reflexively enables it because "it's a websocket app".
      "/api": {
        target: "http://localhost:8080",
        changeOrigin: false,
      },
    },
  },
});
