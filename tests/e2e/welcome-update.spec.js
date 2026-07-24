import { test, expect } from "@playwright/test";

// Кнопка «Проверить обновления» на экране выбора источника (welcome). Этот экран
// рендерится ДО гейта `if (!hasWorkspace) return` в app.js, поэтому обработчик
// должен быть самодостаточным (без showToast/state) — проверяем, что клик даёт
// инлайновый статус и не роняет JS.
// Сервер общий между спеками (reuseExistingServer): другой спек мог оставить
// рабочую область → показался бы wash-экран без welcome-кнопки. Сбрасываем.
test.beforeEach(async ({ page }) => {
  await page.request.post("/workspace/reset");
});

function mockUpdateCheck(page, payload) {
  return page.route("**/api/update-check", (route) => route.fulfill({ json: payload }));
}

const status = (page) => page.locator("[data-check-updates-welcome-status]");

test("welcome: актуальная версия — инлайновый статус, без ошибок JS", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await mockUpdateCheck(page, {
    current: "1.1.12",
    latest: "1.1.12",
    update_available: false,
    url: "",
    installable: false,
  });

  await page.goto("/");
  await page.click("[data-check-updates-welcome]");

  await expect(status(page)).toHaveText("Установлена последняя версия.", { timeout: 15000 });
  expect(errors).toEqual([]);
});

test("welcome: доступное обновление показывается в статусе", async ({ page }) => {
  await mockUpdateCheck(page, {
    current: "1.1.12",
    latest: "1.2.0",
    update_available: true,
    url: "https://github.com/urev11ch/dashboard/releases",
    installable: false,
  });

  await page.goto("/");
  await page.click("[data-check-updates-welcome]");

  await expect(status(page)).toContainText("Доступно обновление 1.2.0", { timeout: 15000 });
});

test("welcome: сбой проверки не оставляет кнопку залипшей", async ({ page }) => {
  await page.route("**/api/update-check", (route) => route.abort());

  await page.goto("/");
  const button = page.locator("[data-check-updates-welcome]");
  await button.click();

  await expect(status(page)).toHaveText("Не удалось проверить обновления.", { timeout: 15000 });
  await expect(button).toBeEnabled();
  await expect(button).toHaveText("Проверить обновления");
});
