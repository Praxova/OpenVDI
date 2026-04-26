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
