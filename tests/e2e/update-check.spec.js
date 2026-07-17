import { test, expect } from "@playwright/test";
import { ensureAnalysis } from "./helpers.js";

// Проверка обновлений — разовое действие по кнопке в настройках (автопроверки
// при старте нет). Ответ /api/update-check подменяем: настоящий поход на GitHub
// в тестах не нужен и был бы флаки.

test.beforeEach(async ({ page }) => {
  await ensureAnalysis(page);
});

function mockUpdateCheck(page, payload) {
  return page.route("**/api/update-check", (route) => route.fulfill({ json: payload }));
}

async function openUpdatesSettings(page) {
  await page.goto("/");
  await expect(page.locator("#washList [data-key]").first()).toBeVisible({ timeout: 15000 });
  await page.click("#openSettings");
  await page.click('[data-settings-nav="updates"]');
}

const toasts = (page) => page.locator(".toast-stack .toast");

test("без нажатия кнопки проверка не выполняется", async ({ page }) => {
  let calls = 0;
  await page.route("**/api/update-check", (route) => {
    calls += 1;
    route.fulfill({ json: { current: "1.1.4", latest: "1.2.0", update_available: true, url: "", installable: false } });
  });

  await openUpdatesSettings(page);
  // Страница загрузилась и настройки открыты — запроса быть не должно.
  expect(calls).toBe(0);

  await page.click("[data-check-updates]");
  await expect(toasts(page).filter({ hasText: "Доступно обновление 1.2.0" })).toBeVisible({ timeout: 15000 });
  expect(calls).toBe(1);
});

test("доступное обновление показывает тост и панель", async ({ page }) => {
  await mockUpdateCheck(page, {
    current: "1.1.4",
    latest: "1.2.0",
    update_available: true,
    url: "https://github.com/urev11ch/dashboard/releases",
    installable: false,
  });

  await openUpdatesSettings(page);
  await page.click("[data-check-updates]");

  await expect(toasts(page).filter({ hasText: "Доступно обновление 1.2.0" })).toBeVisible({ timeout: 15000 });
  await expect(page.locator("[data-update-panel]")).toContainText("Доступно обновление 1.2.0");
});

test("пустой latest не выдаётся за актуальную версию", async ({ page }) => {
  // Бэкенд глушит ошибки запроса к GitHub и отдаёт latest="" — это «не
  // выяснили» (нет сети или релизов), а не «установлена последняя версия».
  // Ложное «всё актуально» скрыло бы от пользователя важное обновление.
  await mockUpdateCheck(page, { current: "1.1.4", latest: "", update_available: false, url: "", installable: false });

  await openUpdatesSettings(page);
  await page.click("[data-check-updates]");

  await expect(toasts(page).filter({ hasText: "Не удалось проверить обновления" })).toBeVisible({ timeout: 15000 });
  await expect(toasts(page).filter({ hasText: "Установлена последняя версия" })).toHaveCount(0);
});

test("известный latest без обновления подтверждает актуальность", async ({ page }) => {
  await mockUpdateCheck(page, { current: "1.1.4", latest: "1.1.4", update_available: false, url: "", installable: false });

  await openUpdatesSettings(page);
  await page.click("[data-check-updates]");

  await expect(toasts(page).filter({ hasText: "Установлена последняя версия" })).toBeVisible({ timeout: 15000 });
});

test("кнопка блокируется на время проверки и возвращается в исходное", async ({ page }) => {
  await page.route("**/api/update-check", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    route.fulfill({ json: { current: "1.1.4", latest: "1.1.4", update_available: false, url: "", installable: false } });
  });

  await openUpdatesSettings(page);
  const button = page.locator("[data-check-updates]");
  await button.click();

  // Пока запрос в полёте — кнопка недоступна: иначе клики наплодят
  // параллельных походов к GitHub.
  await expect(button).toBeDisabled();
  await expect(button).toHaveText("Проверяю…");

  await expect(button).toBeEnabled({ timeout: 15000 });
  await expect(button).toHaveText("Проверить");
});

test("сбой проверки не оставляет кнопку залипшей", async ({ page }) => {
  await page.route("**/api/update-check", (route) => route.abort());

  await openUpdatesSettings(page);
  const button = page.locator("[data-check-updates]");
  await button.click();

  await expect(toasts(page).filter({ hasText: "Не удалось проверить обновления" })).toBeVisible({ timeout: 15000 });
  await expect(button).toBeEnabled();
  await expect(button).toHaveText("Проверить");
});
