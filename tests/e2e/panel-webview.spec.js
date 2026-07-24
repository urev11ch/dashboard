import { test, expect } from "@playwright/test";

// Обнаружение панели → попап → веб-просмотр. Обнаружение мокаем (реальный скан
// сети в тестах не выполнить). Сброс рабочей области — чтобы показался welcome.
test.beforeEach(async ({ page }) => {
  await page.request.post("/workspace/reset");
  await page.route("**/api/ftp/discover", (route) =>
    route.fulfill({
      json: {
        scanned: 253,
        ftp_hosts: 1,
        network: "192.168.1.0/24",
        panels: [
          {
            host: "192.168.1.88",
            port: 21,
            banner: "",
            name: "cMT-3C6F",
            web_scheme: "http",
            mac: "00:0c:26:11:3c:6f",
            mac_weintek: true,
            confirmed_weintek: true,
            likely_weintek: true,
          },
        ],
      },
    }),
  );
});

test("список: строка вида «Weintek cMT-3C6F (IP)»", async ({ page }) => {
  await page.goto("/");
  await page.click("[data-ftp-discover]");
  const row = page.locator(".ftp-discover-item");
  await expect(row).toHaveText("Weintek cMT-3C6F (192.168.1.88)", { timeout: 15000 });
});

test("попап: имя по умолчанию, лейбл «Пароль», без IP:21", async ({ page }) => {
  await page.goto("/");
  await page.click("[data-ftp-discover]");
  await page.click(".ftp-discover-item");

  const modal = page.locator(".ftp-connect-modal");
  await expect(modal).toBeVisible();
  await expect(modal.locator(".ftp-connect-title")).toHaveText("Weintek cMT-3C6F");
  await expect(modal.locator('input[name="label"]')).toHaveValue("Weintek cMT-3C6F");
  // Лейбл пароля — ровно «Пароль», без «по умолчанию 111111».
  await expect(modal.getByText("Пароль", { exact: true })).toBeVisible();
  await expect(modal.getByText(":21")).toHaveCount(0);
  await expect(modal.getByRole("button", { name: "Веб-просмотр" })).toBeVisible();
});

test("веб-просмотр: открывает оверлей с iframe /app/dashboard", async ({ page }) => {
  await page.goto("/");
  await page.click("[data-ftp-discover]");
  await page.click(".ftp-discover-item");
  await page.getByRole("button", { name: "Веб-просмотр" }).click();

  const overlay = page.locator(".panel-webview");
  await expect(overlay).toBeVisible();
  await expect(overlay.locator("iframe")).toHaveAttribute(
    "src",
    "http://192.168.1.88/app/dashboard",
  );
  await expect(overlay.getByRole("button", { name: "Открыть в браузере" })).toBeVisible();
  // «Назад» закрывает оверлей.
  await overlay.getByRole("button", { name: "← Назад" }).click();
  await expect(page.locator(".panel-webview")).toHaveCount(0);
});
