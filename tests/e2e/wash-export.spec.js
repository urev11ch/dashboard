import { test, expect } from "@playwright/test";
import { ensureAnalysis, openWashList } from "./helpers.js";

// Выгрузка журнала в .xlsx: кнопка отдаёт файл браузеру (в десктоп-сборке тот же
// ответ уходит в мост save_wash_export — он проверяется юнит-тестами Python).
test.beforeEach(async ({ page }) => {
  await ensureAnalysis(page);
});

test("кнопка выгружает книгу по отобранным мойкам", async ({ page }) => {
  await openWashList(page);

  const downloadPromise = page.waitForEvent("download");
  await page.locator("#exportXlsx").click();
  const download = await downloadPromise;

  expect(download.suggestedFilename()).toMatch(/\.xlsx$/);

  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream) {
    chunks.push(chunk);
  }
  const content = Buffer.concat(chunks);
  // PK — сигнатура zip: .xlsx это zip-контейнер, пустой/битый ответ её не даст.
  expect(content.subarray(0, 2).toString("latin1")).toBe("PK");
  expect(content.length).toBeGreaterThan(1000);

  await expect(page.locator(".toast--success")).toContainText("Выгружено моек");
});

test("выгрузка идёт по текущему фильтру, а не по всему архиву", async ({ page }) => {
  await openWashList(page);
  const total = await page.locator("#washList [data-key]").count();
  expect(total).toBeGreaterThan(1);

  // Сужаем список поиском по объекту первой строки.
  const firstObject = await page
    .locator("#washList [data-key]")
    .first()
    .locator(".wash-cell")
    .nth(1)
    .innerText();
  await page.locator("#searchInput").fill(firstObject.trim());
  await expect
    .poll(async () => page.locator("#washList [data-key]").count())
    .toBeLessThan(total);
  const filtered = await page.locator("#washList [data-key]").count();

  const [request] = await Promise.all([
    page.waitForRequest((candidate) => candidate.url().includes("/api/wash-export")),
    page.waitForEvent("download"),
    page.locator("#exportXlsx").click(),
  ]);

  const keys = JSON.parse(request.postData() || "{}").keys || [];
  expect(keys).toHaveLength(filtered);
});

test("пустой список не отправляет запрос", async ({ page }) => {
  await openWashList(page);
  await page.locator("#searchInput").fill("такого-объекта-нет-12345");
  await expect.poll(async () => page.locator("#washList [data-key]").count()).toBe(0);

  let requested = false;
  page.on("request", (candidate) => {
    if (candidate.url().includes("/api/wash-export")) {
      requested = true;
    }
  });

  await page.locator("#exportXlsx").click();
  await expect(page.locator(".toast")).toContainText("Нет моек для выгрузки");
  expect(requested).toBe(false);
});
