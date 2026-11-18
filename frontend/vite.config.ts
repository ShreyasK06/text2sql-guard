/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// VITE_BASE sets the public path (GitHub Pages uses /text2sql-guard/). Every runtime URL is built
// from import.meta.env.BASE_URL, so the same bundle works under any base.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "VITE_");
  return {
    base: env.VITE_BASE || "./",
    plugins: [react()],
    server: { port: 5173 },
    test: { environment: "jsdom", globals: false, include: ["src/**/*.test.{ts,tsx}"] },
  };
});
