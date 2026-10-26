import { type ChildProcess, spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

/** Экран без нарушений доступности (WCAG 2.1 A/AA) и без горизонтальной прокрутки на ширине проекта. */
async function checkScreen(page: Page, name: string) {
  const { violations } = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const found = violations.flatMap((v) =>
    v.nodes.map((n) => `${v.id}: ${n.target.join(" ")} — ${n.failureSummary?.split("\n")[1]?.trim() ?? ""}`),
  );
  expect(found, `axe на экране «${name}»`).toEqual([]);
  const widths = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
  expect(widths[0], `горизонтальная прокрутка на экране «${name}»`).toBeLessThanOrEqual(widths[1] ?? 0);
}

test("пример → сборка → прогон → объяснения → сравнение → what-if", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Планируйте");
  // Пример — в карточке нового сценария: одноимённые строки есть и в списке прогонов
  await page
    .getByRole("region", { name: /Соберите DAG/ })
    .getByRole("button", { name: /^urgent_failure/ })
    .click();

  await expect(page).toHaveURL(/\/builder$/);
  await expect(page.getByText("Вход корректен")).toBeVisible();
  await expect(page.getByText("DAG · 11 ЗАДАЧ")).toBeVisible();
  await checkScreen(page, "Сборка");
  await page.getByRole("button", { name: "Запустить" }).click();

  await expect(page).toHaveURL(/\/runs\/[0-9a-f]{16}$/);
  await expect(page.getByRole("heading", { level: 1, name: "urgent_failure" })).toBeVisible();
  await expect(page.getByText("Makespan").locator("..")).toContainText("22 / 21 ч");
  await expect(page.getByRole("button", { name: /^pretrain_vit, попытка 1/ })).toBeVisible();
  await checkScreen(page, "Прогон");
  await page.getByRole("link", { name: "Почему 22, а не 21?" }).click();

  await expect(page).toHaveURL(/\/explain\/[0-9a-f]{16}$/);
  await expect(page.getByRole("heading", { level: 1, name: "Почему 22 ч" })).toBeVisible();
  await expect(page.getByText("+ 1 ПОТЕРЯ ПРИ ОТКАЗЕ")).toBeVisible();
  await checkScreen(page, "Объяснения");
  await page.getByRole("link", { name: "Что если…" }).click();

  await expect(page).toHaveURL(/\/compare$/);
  await expect(page.getByText(/^(Лучше всех|По метрике)/)).toBeVisible({ timeout: 60_000 });
  await checkScreen(page, "Сравнение");

  const whatif = page.getByRole("region", { name: "ЧТО ЕСЛИ…" });
  await whatif.getByRole("button", { name: /^Посчитать \d+ вариант/ }).click();
  const noFailure = whatif.getByRole("article").filter({ hasText: "Без отказов node-A" });
  await expect(noFailure).toContainText("−1 ч", { timeout: 60_000 });
  await checkScreen(page, "What-if");
  await noFailure.getByRole("link", { name: "Открыть вариант" }).click();

  await expect(page).toHaveURL(/\/runs\/[0-9a-f]{16}\?from=[0-9a-f]{16}$/);
  await expect(page.getByText(/из прогона/)).toBeVisible();
  await expect(page.getByText("Makespan").locator("..")).toContainText("21 / 21 ч");
});

test("ноутбук без GPU: ресурсы, загрузка и простой по ядрам и памяти", async ({ page }) => {
  await page.goto("/");
  await page
    .getByRole("region", { name: /Соберите DAG/ })
    .getByRole("button", { name: /^laptop/ })
    .click();
  await expect(page.getByText("Вход корректен")).toBeVisible();
  await expect(page.getByRole("group", { name: "train_small, 40 МИН, 6 CPU · 6 ГБ" })).toBeVisible();
  await expect(page.getByText("395 CPU·мин работы")).toBeVisible();
  await page.getByRole("button", { name: "Запустить" }).click();

  await expect(page.getByRole("heading", { level: 1, name: "laptop" })).toBeVisible();
  await expect(page.getByText("Загрузка CPU").locator("..")).toContainText("55");
  await expect(page.getByText("Загрузка ОЗУ").locator("..")).toContainText("45");
  await page.getByRole("link", { name: /^Почему 90/ }).click();
  const idle = page.getByRole("region", { name: "Почему простаивают узлы" });
  await idle.getByRole("radio", { name: "ОЗУ" }).click();
  await expect(idle.getByText(/ГБ·мин/)).toBeVisible();
  await checkScreen(page, "Объяснения: ноутбук");
});

test("остальные экраны доступны и не шире экрана", async ({ page }) => {
  for (const [path, heading] of [
    ["/", /Планируйте/],
    ["/cluster", /Кластер/],
    ["/system", /Общий язык интерфейса/],
    ["/containers", /Контейнеры/],
  ] as const) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toContainText(heading);
    await checkScreen(page, path);
  }
});

test.describe("на агенте fake", () => {
  let agent: ChildProcess | undefined;

  test.beforeAll(() => {
    // Агент без контейнеров: работа идёт по часам стены в масштабе прогона
    const root = resolve("../..");
    const python = join(root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
    const state = mkdtempSync(join(tmpdir(), "expsched-agent-"));
    agent = spawn(
      python,
      [
        "-m",
        "expsched.agent",
        "--scheduler",
        String(test.info().project.use.baseURL),
        "--id",
        "laptop",
      ].concat(["--runtime", "fake", "--cpu", "8", "--ram-mb", "16384", "--state", state]),
      {
        cwd: root,
        env: { ...process.env, EXPSCHED_AGENT_TOKEN: "e2e-token" },
        stdio: ["ignore", "ignore", "inherit"],
      },
    );
  });

  test.afterAll(() => {
    agent?.kill();
  });

  test("shadow: контейнеры, план рядом с фактом, проигрывание журнала", async ({ page }) => {
    await page.goto("/");
    await page
      .getByRole("region", { name: /Соберите DAG/ })
      .getByRole("button", { name: /^laptop/ })
      .click();
    await expect(page.getByText("Вход корректен")).toBeVisible();
    await page.goto("/containers");

    await page.getByRole("radio", { name: "Shadow" }).click();
    await page.getByRole("radio", { name: "100 мс" }).click();
    await page.getByRole("button", { name: "Назначить узлам" }).click();
    await page.getByRole("button", { name: "Запустить на агентах" }).click();

    await expect(page).toHaveURL(/\/containers\/[0-9a-f]{16}$/);
    await expect(page.getByText(/^закончен · момент модели \d+$/)).toBeVisible({ timeout: 60_000 });
    await expect(
      page.getByRole("button", { name: /^report, попытка 1 на laptop: завершён, 100%$/ }),
    ).toBeVisible();
    const plan = page.getByRole("region", { name: "План и факт", exact: true });
    await expect(plan.getByText(/^Makespan: план 90 мин · факт \d+ мин/)).toBeVisible();
    await checkScreen(page, "Контейнеры: прогон");
    await page.getByRole("link", { name: "Открыть прогон" }).click();

    await expect(page.getByRole("heading", { level: 1, name: "laptop" })).toBeVisible();
    await expect(page.getByRole("region", { name: "План и факт", exact: true })).toBeVisible();
    const replay = page.getByRole("region", { name: "Проигрывание" });
    await replay.getByRole("button", { name: "Проиграть журнал" }).click();
    await expect(replay.getByRole("status")).toContainText("Решения совпали");
    await checkScreen(page, "Прогон на агентах");
  });
});
