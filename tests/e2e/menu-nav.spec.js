import { test, expect } from "@playwright/test";
import { ensureAnalysis } from "./helpers.js";

// «Главное меню» ведёт на экран выбора источника БЕЗ разрыва соединения
// (?view=menu). На таком меню при загруженной рабочей области wash-JS не
// стартует (hasWorkspace=false), а вернуться к данным можно навигацией на /.
test("меню при загруженной области: показ без ошибок JS, возврат на /", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await ensureAnalysis(page); // загружает папку-фикстуру → wash-экран

  await page.goto("/?view=menu");
  await expect(page.locator(".welcome-shell")).toBeVisible();
  await expect(page.locator(".wash-screen")).toHaveCount(0);
  expect(errors).toEqual([]);

  await page.goto("/");
  await expect(page.locator(".wash-screen")).toBeVisible();
});

test("обычный wash-экран без view=menu", async ({ page }) => {
  await ensureAnalysis(page);
  await page.goto("/");
  await expect(page.locator(".wash-screen")).toBeVisible();
  await expect(page.locator(".welcome-shell")).toHaveCount(0);
});
