import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// Identifier of this build: the UI compares it with /version.json to detect
// that a newer version was deployed while a page is open (reload banner).
const BUILD_ID = process.env.BUILD_ID ?? Date.now().toString(36);

const versionFile = (): Plugin => ({
  name: "version-file",
  apply: "build",
  generateBundle() {
    this.emitFile({ type: "asset", fileName: "version.json", source: JSON.stringify({ build: BUILD_ID }) + "\n" });
  },
});

// In development, /api is proxied to the API (tunnel or local port 8000).
export default defineConfig({
  plugins: [react(), versionFile()],
  define: { __BUILD_ID__: JSON.stringify(BUILD_ID) },
  server: {
    proxy: { "/api": process.env.API_URL ?? "http://127.0.0.1:8000" },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 900 },
  test: { environment: "node" },
});
