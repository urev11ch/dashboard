import { test, expect } from "@playwright/test";

// Единая умная кнопка обновления на экране выбора источника: «Проверить
// обновления» → при наличии устанавливаемого апдейта та же кнопка становится
// «Установить обновление». Самодостаточна (welcome — до гейта hasWorkspace).
// Ответ /api/update-check мокаем (реальный GitHub в тестах не нужен).
test.beforeEach(async ({ page }) => {
  await page.request.post("/workspace/reset");
});

function mockUpdateCheck(page, payload) {
  return page.route("**/api/update-check", (route) => route.fulfill({ json: payload }));
}

const button = (page) => page.locator("[data-update-btn]");

test("welcome: «Выбрать папку» без pywebview показывает тост, не падает (toastRoot до гейта)", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto("/");
  await page.click('[data-source-tab="folder"]');
  await page.click("[data-folder-picker]"); // в браузере choose_folder недоступен → тост об ошибке
  await expect(page.locator(".toast-stack .toast")).toBeVisible({ timeout: 15000 });
  expect(errors).toEqual([]);
});
const status = (page) => page.locator("[data-update-status]");

test("актуальная версия — статус, кнопка не меняется, без ошибок JS", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await mockUpdateCheck(page, {
    current: "1.1.17",
    latest: "1.1.17",
    update_available: false,
    url: "",
    installable: false,
  });

  await page.goto("/");
  await button(page).click();

  await expect(status(page)).toHaveText("Установлена последняя версия.", { timeout: 15000 });
  await expect(button(page)).toHaveText("Проверить обновления");
  expect(errors).toEqual([]);
});

test("обновление без установщика — ссылка на Releases, кнопка не меняется", async ({ page }) => {
  await mockUpdateCheck(page, {
    current: "1.1.17",
    latest: "1.2.0",
    update_available: true,
    url: "https://github.com/urev11ch/dashboard/releases",
    installable: false,
  });

  await page.goto("/");
  await button(page).click();

  await expect(status(page)).toContainText("Доступно обновление 1.2.0", { timeout: 15000 });
  await expect(button(page)).toHaveText("Проверить обновления");
});

test("устанавливаемое обновление → кнопка становится «Установить», установка идёт", async ({
  page,
}) => {
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
    current: "1.1.17",
    latest: "1.1.18",
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

  await page.goto("/");
  await button(page).click();

  await expect(button(page)).toHaveText("Установить обновление", { timeout: 15000 });
  await expect(status(page)).toContainText("Доступно обновление 1.1.18");

  await button(page).click();
  await expect(status(page)).toHaveText(/Запускаю установку/, { timeout: 15000 });
  expect(await page.evaluate(() => window.__installCalled)).toBe(true);
});

test("сбой проверки — статус ошибки, кнопка активна", async ({ page }) => {
  await page.route("**/api/update-check", (route) => route.abort());

  await page.goto("/");
  await button(page).click();

  await expect(status(page)).toHaveText("Не удалось проверить обновления.", { timeout: 15000 });
  await expect(button(page)).toBeEnabled();
  await expect(button(page)).toHaveText("Проверить обновления");
});
