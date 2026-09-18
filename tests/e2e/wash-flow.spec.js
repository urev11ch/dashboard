import { test, expect } from "@playwright/test";
import { ensureAnalysis, openWashList } from "./helpers.js";

test.beforeEach(async ({ page }) => {
  await ensureAnalysis(page);
});

test("открытие мойки показывает график", async ({ page }) => {
  await openWashList(page);
  const firstRow = page.locator("#washList [data-key]").first();
  await firstRow.click();

  // Модалка мойки с SVG-графиком (WashChart.mount отрисовал серии).
  const chartSvg = page.locator("[data-chart-host] svg, .chart-modal-panel svg").first();
  await expect(chartSvg).toBeVisible({ timeout: 15000 });
  await expect(chartSvg.locator("path")).not.toHaveCount(0);
});

test("смена сортировки перерисовывает список", async ({ page }) => {
  await openWashList(page);

  const firstBefore = await page.locator("#washList [data-key]").first().getAttribute("data-key");

  await page.locator('#sortOptions [data-sort-value="object_asc"]').click();
  await expect(page.locator('#sortOptions [data-sort-value="object_asc"]')).toHaveClass(/is-active/);

  // Список остаётся непустым; сортировка по объекту (А-Я) не должна падать
  // (регресс: localeCompare на undefined роняла getFilteredRows).
  await expect(page.locator("#washList [data-key]").first()).toBeVisible();
  const firstAfter = await page.locator("#washList [data-key]").first().getAttribute("data-key");
  expect(typeof firstAfter).toBe("string");
  // Порядок мог измениться — проверяем, что рендер живой, а не застыл с ошибкой.
  expect(firstAfter.length).toBeGreaterThan(0);
  void firstBefore;
});

test("сбой обновления разблокирует кнопку и показывает ошибку", async ({ page }) => {
  await openWashList(page);

  // Имитируем зависший/упавший бэкенд на обновлении: fetchWithTimeout должен
  // отклонить промис, catch — показать ошибку экрана, finally — снять disabled.
  await page.route("**/api/workspace/refresh", (route) => route.abort());

  const refreshButton = page.getByRole("button", { name: "Обновить данные" });
  await refreshButton.click();

  // Баннер ошибки экрана появляется, и кнопка снова активна (не залипла).
  await expect(page.locator("#screenErrorNotice")).toBeVisible({ timeout: 15000 });
  await expect(refreshButton).toBeEnabled({ timeout: 15000 });
});

test("дата и время везде в одном формате ДД.ММ.ГГГГ ЧЧ:ММ:СС", async ({ page }) => {
  await openWashList(page);

  // Регресс, от которого защищаемся: карточка мойки и печатный отчёт писали
  // «14.07.2026. 12.00.00» (точки вместо двоеточий), диагностика — сырое
  // «2026-07-14 12:00:00» с бэкенда, список — третий вариант.
  const FORMAT = /^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}$/;

  const listTime = await page.locator("#washList [data-key] .wash-entry-time").first().innerText();
  expect(listTime.trim()).toMatch(FORMAT);

  await page.locator("#washList [data-key]").first().click();
  const modalRow = page.locator("tr", { has: page.getByRole("rowheader", { name: "Начало мойки" }) });
  await expect(modalRow.locator("td")).toHaveText(FORMAT, { timeout: 15000 });
});

test("результат мойки по умолчанию скрыт и включается тумблером", async ({ page }) => {
  // Свежий профиль: ключа opticipShowWashResultV1 в localStorage нет.
  await openWashList(page);

  const badge = page.locator("#washList [data-key] .wash-cell--status .badge").first();
  await expect(badge).toBeHidden();

  await page.locator("#openSettings").click();
  const toggle = page.locator("[data-setting-wash-result]");
  await expect(toggle).not.toBeChecked();

  await toggle.check();
  await expect(badge).toBeVisible();
});
