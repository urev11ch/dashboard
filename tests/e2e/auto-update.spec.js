import { test, expect } from "@playwright/test";
import { ensureAnalysis } from "./helpers.js";

// Автообновление при запуске: проверка → скачивание → установка, без нажатий.
// Мост pywebview подделываем (в браузере его нет), ответы сервера — тоже:
// настоящий поход на GitHub был бы флаки.

const UPDATE_AVAILABLE = {
  current: "1.1.30",
  latest: "1.1.31",
  update_available: true,
  installable: true,
  url: "",
};

async function fakeBridge(page) {
  await page.addInitScript(() => {
    window.__installCalls = 0;
    window.pywebview = {
      api: {
        install_update: async () => {
          window.__installCalls += 1;
          return { ok: true };
        },
      },
    };
  });
}

async function mockUpdateApi(page, { job = { status: "ready", path: "x", downloaded: 1, total: 1 } } = {}) {
  await page.route("**/api/update-check", (route) => route.fulfill({ json: UPDATE_AVAILABLE }));
  await page.route("**/api/update/download", (route) => route.fulfill({ json: { job } }));
  await page.route("**/api/update/job", (route) => route.fulfill({ json: job }));
}

test("обновление ставится само, без нажатий", async ({ page }) => {
  await ensureAnalysis(page);
  await fakeBridge(page);
  await mockUpdateApi(page);

  await page.goto("/");
  await expect(page.locator("[data-auto-update-banner]")).toContainText("1.1.31");
  await expect.poll(() => page.evaluate(() => window.__installCalls)).toBe(1);
});

test("выключённая настройка отменяет автообновление", async ({ page }) => {
  await ensureAnalysis(page);
  await fakeBridge(page);
  await mockUpdateApi(page);

  let checks = 0;
  await page.route("**/api/settings", (route) => {
    if (route.request().method() !== "GET") {
      return route.continue();
    }
    return route.fulfill({ json: { settings: { auto_update_enabled: false } } });
  });
  await page.route("**/api/update-check", (route) => {
    checks += 1;
    return route.fulfill({ json: UPDATE_AVAILABLE });
  });

  await page.goto("/");
  await page.waitForTimeout(1500);
  expect(checks).toBe(0);
  await expect(page.locator("[data-auto-update-banner]")).toHaveCount(0);
  expect(await page.evaluate(() => window.__installCalls)).toBe(0);
});

test("отказ от установки оставляет приложение работать", async ({ page }) => {
  await ensureAnalysis(page);
  await page.addInitScript(() => {
    window.__installCalls = 0;
    window.pywebview = {
      api: {
        install_update: async () => {
          window.__installCalls += 1;
          // Так выглядит нажатие «Нет» в запросе UAC.
          return { ok: false, error: "Установка отменена." };
        },
      },
    };
  });
  await mockUpdateApi(page);

  await page.goto("/");
  await expect.poll(() => page.evaluate(() => window.__installCalls)).toBe(1);
  // Баннер убран, окно живо и пользоваться приложением можно.
  await expect(page.locator("[data-auto-update-banner]")).toHaveCount(0);
  await expect(page.locator("#openSettings")).toBeVisible();
});

test("в браузере (без моста) автообновление не запускается", async ({ page }) => {
  await ensureAnalysis(page);
  let checks = 0;
  await page.route("**/api/update-check", (route) => {
    checks += 1;
    return route.fulfill({ json: UPDATE_AVAILABLE });
  });

  await page.goto("/");
  await page.waitForTimeout(1500);
  expect(checks).toBe(0);
});
