import { defineConfig } from "vite";
import preact from "@preact/preset-vite";

// Builds straight into the Python package so the wheel ships the UI and
// FastAPI serves it from infinance/webui — zero runtime CDN, ever.
export default defineConfig({
  plugins: [preact()],
  build: {
    outDir: "../infinance/webui",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
