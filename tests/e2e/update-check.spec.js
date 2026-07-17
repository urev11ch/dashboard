import { test, expect } from "@playwright/test";
import { ensureAnalysis } from "./helpers.js";

// Проверка обновлений: checkForUpdates() вызывается при старте и по включению
// тумблера. Ответ /api/update-check подменяем — настоящий поход на GitHub в
// тестах не нужен и был бы флаки.

test.beforeEach(async ({ page }) => {
  await ensureAnalysis(page);
});

function mockUpdateCheck(page, payload) {
  return page.route("**/api/update-check", (route) =>
    route.fulfill({ json: payload }),
  );
}

test("доступное обновление показывает тост с версией", async ({ page }) => {
  await mockUpdateCheck(page, {
    enabled: true,
    current: "1.1.0",
    latest: "1.2.0",
    update_available: true,
    url: "https://github.com/urev11ch/dashboard/releases",
  });

  await page.goto("/");
  await expect(page.locator(".toast-stack .toast").filter({ hasText: "Доступно обновление 1.2.0" })).toBeVisible({
    timeout: 15000,
  });
});

test("пустой latest не выдаётся за актуальную версию", async ({ page }) => {
  // Бэкенд глушит ошибки запроса к GitHub и отдаёт latest="" — это «не
  // выяснили» (нет сети или релизов), а не «установлена последняя версия».
  // Ложное «всё актуально» скрыло бы от пользователя важное обновление.
  await mockUpdateCheck(page, {
    enabled: true,
    current: "1.1.0",
    latest: "",
    update_available: false,
    url: "",
  });

  await page.goto("/");
  await expect(page.locator("#washList [data-key]").first()).toBeVisible({ timeout: 15000 });

  await page.click("#openSettings");
  await page.click('[data-settings-nav="updates"]');

  const toggle = page.locator("[data-setting-check-updates]");
  await expect(toggle).toBeEnabled();
  if (await toggle.isChecked()) {
    await toggle.uncheck();
  }
  await toggle.check();

  const toasts = page.locator(".toast-stack .toast");
  await expect(toasts.filter({ hasText: "Не удалось проверить обновления" })).toBeVisible({ timeout: 15000 });
  await expect(toasts.filter({ hasText: "Установлена последняя версия" })).toHaveCount(0);
});

test("известный latest без обновления подтверждает актуальность", async ({ page }) => {
  await mockUpdateCheck(page, {
    enabled: true,
    current: "1.1.0",
    latest: "1.1.0",
    update_available: false,
    url: "",
  });

  await page.goto("/");
  await expect(page.locator("#washList [data-key]").first()).toBeVisible({ timeout: 15000 });

  await page.click("#openSettings");
  await page.click('[data-settings-nav="updates"]');

  const toggle = page.locator("[data-setting-check-updates]");
  if (await toggle.isChecked()) {
    await toggle.uncheck();
  }
  await toggle.check();

  await expect(
    page.locator(".toast-stack .toast").filter({ hasText: "Установлена последняя версия" }),
  ).toBeVisible({ timeout: 15000 });
});
