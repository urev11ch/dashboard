import { test, expect } from "@playwright/test";
import { ensureAnalysis, openWashList } from "./helpers.js";

// Просмотр сырого содержимого .db: окно открывается, показывает таблицы фикстуры
// и листается по страницам.
test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await ensureAnalysis(page);
});

test("окно показывает таблицы базы и строки", async ({ page }) => {
  await openWashList(page);
  await page.locator("#openDbBrowser").click();

  await expect(page.locator(".db-browser-table tbody tr").first()).toBeVisible();
  await expect(page.locator("[data-db-table]")).not.toHaveCount(0);
  // Стартуем с таблицы `data` — она содержательная.
  await expect(page.locator("[data-db-table].is-active")).toHaveText(/data/);
  await expect(page.locator(".db-browser-position")).toContainText("из");
});

test("заголовки колонок подписаны названиями тегов панели", async ({ page }) => {
  await openWashList(page);
  await page.locator("#openDbBrowser").click();
  await expect(page.locator(".db-browser-table thead")).toContainText("Концентрация возврата");
});

test("листание страниц меняет строки", async ({ page }) => {
  await openWashList(page);
  await page.locator("#openDbBrowser").click();
  await expect(page.locator(".db-browser-table tbody tr").first()).toBeVisible();

  await expect(page.locator(".db-browser-position")).toHaveText(/^1–/);
  // Шаг листания = выбранному размеру страницы, а не зашитой сотне.
  const pageSize = Number(await page.locator("[data-db-page-size]").inputValue());
  const firstBefore = await page.locator(".db-browser-table tbody tr").first().textContent();
  await page.locator('[data-db-page="next"]').click();
  await expect(page.locator(".db-browser-position")).toHaveText(new RegExp(`^${pageSize + 1}–`));
  const firstAfter = await page.locator(".db-browser-table tbody tr").first().textContent();
  expect(firstAfter).not.toBe(firstBefore);
});

test("Escape закрывает окно", async ({ page }) => {
  await openWashList(page);
  await page.locator("#openDbBrowser").click();
  await expect(page.locator(".db-browser-panel")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator(".db-browser-panel")).toHaveCount(0);
});

test("смена размера страницы меняет число строк", async ({ page }) => {
  await openWashList(page);
  await page.locator("#openDbBrowser").click();
  await expect(page.locator(".db-browser-table tbody tr").first()).toBeVisible();

  await page.locator("[data-db-page-size]").selectOption("100");
  await expect(page.locator(".db-browser-position")).toHaveText(/^1–100/);
  await expect(page.locator(".db-browser-table tbody tr")).toHaveCount(100);
});
