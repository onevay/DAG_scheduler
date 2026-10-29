import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { defineConfig, devices } from "@playwright/test";

// Сборка клиента и API одним сервером, как в `make run`, но на своём порту и пустом каталоге прогонов:
// проверки не зависят от того, что лежит у разработчика
const PORT = 8010;
const URL = `http://127.0.0.1:${PORT}`;
const runsDir = mkdtempSync(join(tmpdir(), "expsched-e2e-"));

export default defineConfig({
  testDir: "e2e",
  // Путь один и идёт по шагам: прогон, который создала сборка, открывают объяснения и сравнение
  fullyParallel: false,
  workers: 1,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: URL,
    locale: "ru-RU",
    // Без анимаций: axe иначе меряет контраст посреди плавного появления и видит полупрозрачный текст
    reducedMotion: "reduce",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "phone", use: { ...devices["Desktop Chrome"], viewport: { width: 390, height: 844 } } },
  ],
  webServer: {
    command: `npm run build && uv run --project ../.. --extra api uvicorn expsched.api.app:app --host 127.0.0.1 --port ${PORT}`,
    url: `${URL}/v1/policies`,
    env: {
      EXPSCHED_RUNS_DIR: runsDir,
      EXPSCHED_RUN_WORKERS: "2",
      EXPSCHED_WEB_DIR: resolve("dist"),
      // Прогон на агентах: агента `fake` проверка запускает сама
      EXPSCHED_AGENT_TOKEN: "e2e-token",
    },
    reuseExistingServer: false,
    timeout: 180_000,
  },
});
