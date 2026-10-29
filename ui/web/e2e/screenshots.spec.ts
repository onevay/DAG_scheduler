import { resolve } from "node:path";

import { expect, type Page, test } from "@playwright/test";

// Скриншоты для README: make screenshots. В обычном прогоне e2e пропускаются
test.skip(!process.env.SCREENSHOTS, "только для make screenshots");

const OUT = resolve("..", "..", "docs", "img");
const shot = (page: Page, name: string) => page.screenshot({ path: resolve(OUT, `${name}.png`) });

test("скриншоты экранов", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page
    .getByRole("region", { name: /Соберите DAG/ })
    .getByRole("button", { name: /^ml_hetero/ })
    .click();
  await expect(page.getByText("Вход корректен")).toBeVisible();
  await shot(page, "builder");

  await page.getByRole("button", { name: "Запустить" }).click();
  await expect(page).toHaveURL(/\/runs\/[0-9a-f]{16}$/);
  await expect(page.getByRole("heading", { level: 1, name: "ml_hetero" })).toBeVisible();
  await shot(page, "run");

  await page.goto("/compare");
  await expect(page.getByText(/^(Лучше всех|По метрике)/)).toBeVisible({ timeout: 60_000 });
  await shot(page, "compare");

  await page.goto("/containers");
  await page.getByRole("button", { name: "Запустить демо" }).click();
  await expect(page.getByText(/^закончен · момент модели \d+$/)).toBeVisible({ timeout: 90_000 });
  await shot(page, "containers");
  await page.getByRole("list", { name: "Лента сбоев" }).scrollIntoViewIfNeeded();
  await shot(page, "failures");

  await page.goto("/");
  await shot(page, "overview");
});
