import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Demo storefront (/) + agent console (/console/). The embeddable widget is built separately
// (vite.widget.config.ts) into dist/widget.js.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/v1": { target: "http://localhost:8000", ws: true, changeOrigin: false } },
  },
  build: {
    outDir: "dist",
    rollupOptions: {
      input: { main: fileURLToPath(new URL("index.html", import.meta.url)), console: fileURLToPath(new URL("console/index.html", import.meta.url)) },
    },
  },
});
