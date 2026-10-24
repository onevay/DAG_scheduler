/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1", // как у API: на Windows «localhost» может оказаться только IPv6
    port: 5173,
    // API — на соседнем порту; через прокси клиенту не нужен CORS.
    proxy: { "/v1": "http://127.0.0.1:8000" },
    // Токены и анимации лежат рядом, в ui/design.
    fs: { allow: [".."] },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: { modules: { classNameStrategy: "non-scoped" } },
  },
});
