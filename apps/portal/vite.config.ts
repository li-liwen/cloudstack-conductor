import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The portal is served by the Conductor API from this same origin.
// Runtime deployment identity is never compiled into the bundle.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "dist",
    sourcemap: false,
    target: "es2022",
    assetsDir: "assets",
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8080",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    restoreMocks: true,
  },
});
