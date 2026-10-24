/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// API — на соседнем порту; через прокси клиенту не нужен CORS. Сквозные проверки поднимают свой API и задают его тут.
const api = process.env.EXPSCHED_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1", // как у API: на Windows «localhost» может оказаться только IPv6
    port: 5173,
    proxy: { "/v1": api },
    // Токены и анимации лежат рядом, в ui/design.
    fs: { allow: [".."] },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    proxy: { "/v1": api },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: { modules: { classNameStrategy: "non-scoped" } },
  },
});
