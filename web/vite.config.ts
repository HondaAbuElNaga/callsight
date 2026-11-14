import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", assetsInlineLimit: 0 },
  // ws: true carries the live board WebSocket. The dev server's own origin (port 5173) is NOT in the API's
  // Origin allowlist by default: add it to LIVE_ORIGINS only while developing.
  server: { proxy: { "/api": { target: "http://127.0.0.1:8080", ws: true } } },
});
