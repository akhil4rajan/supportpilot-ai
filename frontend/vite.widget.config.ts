import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

// One self-contained file a customer drops on their site: <script src=".../widget.js" data-public-key="...">
export default defineConfig({
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
  build: {
    outDir: "dist",
    emptyOutDir: false,
    lib: {
      entry: fileURLToPath(new URL("src/widget/index.tsx", import.meta.url)),
      name: "SupportPilot",
      formats: ["iife"],
      fileName: () => "widget.js",
    },
  },
});
