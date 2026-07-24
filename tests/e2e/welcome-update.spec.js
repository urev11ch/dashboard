import { test, expect } from "@playwright/test";

// Обновления теперь внутри диалога «Настройки» на экране выбора источника. Диалог
// рендерится до гейта `if (!hasWorkspace) return`, поэтому обработчик самодостаточен
// (свой fetch, без showToast/state) — проверяем проверку и установку + отсутствие
// ошибок JS. Ответ /api/update-check мокаем (реальный GitHub в тестах не нужен).
test.beforeEach(async ({ page }) => {
  await page.request.post("/workspace/reset");
});

function mockUpdateCheck(page, payload) {
  return page.route("**/api/update-check", (route) => route.fulfill({ json: payload }));
}

const dialog = (page) => page.locator(".ftp-connect-modal");
const status = (page) => dialog(page).locator(".welcome-update-status");
const openSettings = async (page) => {
  await page.goto("/");
  await page.click("[data-welcome-settings]");
  await expect(dialog(page)).toBeVisible();
};

test("настройки: актуальная версия — статус, без ошибок JS", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await mockUpdateCheck(page, {
    current: "1.1.15",
    latest: "1.1.15",
    update_available: false,
    url: "",
    installable: false,
  });

  await openSettings(page);
  await dialog(page).getByRole("button", { name: "Проверить обновления" }).click();

  await expect(status(page)).toHaveText("Установлена последняя версия.", { timeout: 15000 });
  expect(errors).toEqual([]);
});

test("настройки: доступное обновление показывается в статусе", async ({ page }) => {
  await mockUpdateCheck(page, {
    current: "1.1.15",
    latest: "1.2.0",
    update_available: true,
    url: "https://github.com/urev11ch/dashboard/releases",
    installable: false,
  });

  await openSettings(page);
  await dialog(page).getByRole("button", { name: "Проверить обновления" }).click();

  await expect(status(page)).toContainText("Доступно обновление 1.2.0", { timeout: 15000 });
});

test("настройки: установка обновления в один клик (десктоп-мост)", async ({ page }) => {
  await page.addInitScript(() => {
    window.pywebview = {
      api: {
        install_update: async () => {
          window.__installCalled = true;
          return { ok: true };
        },
      },
    };
  });
  await mockUpdateCheck(page, {
    current: "1.1.15",
    latest: "1.1.16",
    update_available: true,
    url: "",
    installable: true,
  });
  await page.route("**/api/update/download", (route) =>
    route.fulfill({ json: { job: { status: "running", downloaded: 0, total: 100 } } }),
  );
  await page.route("**/api/update/job", (route) =>
    route.fulfill({ json: { status: "ready", ready: true, downloaded: 100, total: 100 } }),
  );

  await openSettings(page);
  await dialog(page).getByRole("button", { name: "Проверить обновления" }).click();

  const install = dialog(page).getByRole("button", { name: "Установить обновление" });
  await expect(install).toBeVisible({ timeout: 15000 });
  await install.click();

  await expect(status(page)).toHaveText(/Запускаю установку/, { timeout: 15000 });
  expect(await page.evaluate(() => window.__installCalled)).toBe(true);
});

test("настройки: сбой проверки — статус ошибки, кнопка не залипает", async ({ page }) => {
  await page.route("**/api/update-check", (route) => route.abort());

  await openSettings(page);
  const check = dialog(page).getByRole("button", { name: "Проверить обновления" });
  await check.click();

  await expect(status(page)).toHaveText("Не удалось проверить обновления.", { timeout: 15000 });
  await expect(check).toBeEnabled();
});
