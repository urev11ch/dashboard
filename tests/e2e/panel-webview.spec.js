import { test, expect } from "@playwright/test";

// Флоу панели: поиск → «Добавить панель» (сохранение) → сохранённая панель →
// «Подключиться» → выбор «Веб-просмотр»/«Графики». Обнаружение мокаем.
const DISCOVER = {
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
};

test.beforeEach(async ({ page }) => {
  await page.request.post("/workspace/reset");
  await page.route("**/api/ftp/discover", (route) => route.fulfill({ json: DISCOVER }));
});

test("список: строка «Weintek cMT-3C6F (IP)»", async ({ page }) => {
  await page.goto("/");
  await page.click("[data-ftp-discover]");
  await expect(page.locator(".ftp-discover-item")).toHaveText(
    "Weintek cMT-3C6F (192.168.1.88)",
    { timeout: 15000 },
  );
});

test("попап поиска: имя, «Пароль», «Добавить панель», без веб/архивов и IP:21", async ({
  page,
}) => {
  await page.goto("/");
  await page.click("[data-ftp-discover]");
  await page.click(".ftp-discover-item");

  const modal = page.locator(".ftp-connect-modal");
  await expect(modal.locator(".ftp-connect-title")).toHaveText("Weintek cMT-3C6F");
  await expect(modal.locator('input[name="label"]')).toHaveValue("Weintek cMT-3C6F");
  await expect(modal.getByText("Пароль", { exact: true })).toBeVisible();
  await expect(modal.getByRole("button", { name: "Добавить панель" })).toBeVisible();
  await expect(modal.getByRole("button", { name: "WebView" })).toHaveCount(0);
  await expect(modal.getByText(":21")).toHaveCount(0);
});

test("нет панелей → появляется «Добавить вручную» и раскрывает форму", async ({ page }) => {
  // Роут, зарегистрированный позже beforeEach, побеждает — отдаём пустой список.
  await page.route("**/api/ftp/discover", (route) =>
    route.fulfill({
      json: { scanned: 253, ftp_hosts: 0, network: "192.168.1.0/24", panels: [] },
    }),
  );
  await page.goto("/");
  const manual = page.locator("[data-ftp-manual]");
  await expect(manual).toBeHidden();
  await page.click("[data-ftp-discover]");
  await expect(manual).toBeVisible({ timeout: 15000 });

  await manual.click();
  const form = page.locator("[data-ftp-add]");
  await expect(form).toBeVisible();
  await expect(form.locator("button[type=submit]")).toHaveText("Добавить панель");
});

test.describe("сохранённая панель", () => {
  // Заводим панель через add-эндпоинт, в конце удаляем — не сорим в реестре.
  test.beforeEach(async ({ page }) => {
    await page.request.post("/workspace/ftp-source/add", {
      form: {
        host: "192.168.1.88",
        port: "21",
        password: "111111",
        path: "/datalog",
        passive: "on",
        label: "Weintek cMT-3C6F",
        web_scheme: "http",
      },
    });
  });
  test.afterEach(async ({ page }) => {
    await page.request.post("/workspace/reset"); // снять пометку подключения
    await page.goto("/");
    const del = page.locator(".ftp-source-item form[action*='delete'] button");
    while (await del.count()) {
      page.once("dialog", (d) => d.accept());
      await del.first().click();
      await page.waitForLoadState("networkidle");
    }
  });

  test("«Подключиться» → зелёная строка + WebView/Графики/Отключить, без попапа", async ({
    page,
  }) => {
    await page.goto("/");
    await page.click(".ftp-source-item button:has-text('Подключиться')");

    const item = page.locator(".ftp-source-item");
    await expect(item).toHaveClass(/is-connected/, { timeout: 15000 });
    await expect(item.getByRole("button", { name: "WebView" })).toBeVisible();
    await expect(item.getByRole("button", { name: "Графики" })).toBeVisible();
    await expect(item.getByRole("button", { name: "Отключить" })).toBeVisible();
    // Всплывающего окна выбора нет.
    await expect(page.locator(".ftp-connect-modal")).toHaveCount(0);
  });

  test("«Отключить» возвращает обычные действия", async ({ page }) => {
    await page.goto("/");
    await page.click(".ftp-source-item button:has-text('Подключиться')");
    await expect(page.locator(".ftp-source-item")).toHaveClass(/is-connected/);
    await page.click(".ftp-source-item button:has-text('Отключить')");
    await expect(page.locator(".ftp-source-item")).not.toHaveClass(/is-connected/);
    await expect(page.getByRole("button", { name: "Подключиться" })).toBeVisible();
  });

  test("«Изменить» переименовывает панель в списке", async ({ page }) => {
    await page.goto("/");
    await page.click("[data-panel-rename]");
    const modal = page.locator(".ftp-connect-modal");
    await expect(modal).toBeVisible();
    await modal.locator('input[name="label"]').fill("Цех 5");
    await modal.getByRole("button", { name: "Сохранить" }).click();
    await page.waitForLoadState("networkidle");
    await expect(page.locator(".ftp-source-label")).toHaveText("Цех 5");
  });

  test("WebView подключённой панели открывает окно/вкладку /app/dashboard", async ({
    page,
  }) => {
    // В вебе WebView открывается через window.open (топ-левел), не iframe:
    // EasyWeb запрещает встраивание (X-Frame-Options). Перехватываем open.
    await page.addInitScript(() => {
      window.__opened = [];
      window.open = (u) => {
        window.__opened.push(u);
        return null;
      };
    });

    await page.goto("/");
    await page.click(".ftp-source-item button:has-text('Подключиться')");
    await page.locator(".ftp-source-item").getByRole("button", { name: "WebView" }).click();

    await expect
      .poll(() => page.evaluate(() => window.__opened), { timeout: 15000 })
      .toContain("http://192.168.1.88/app/dashboard");
    await expect(page.locator(".panel-webview")).toHaveCount(0);
  });

  test("WebView: клик — окно приложения, Ctrl+клик — браузер", async ({ page }) => {
    // Стаб моста pywebview: фиксируем, какой метод вызван (окно/браузер).
    await page.addInitScript(() => {
      window.__calls = [];
      window.pywebview = {
        api: {
          open_panel_window: async (p) => {
            window.__calls.push(["window", p.url]);
            return { ok: true };
          },
          open_external: async (p) => {
            window.__calls.push(["browser", p.url]);
            return { ok: true };
          },
        },
      };
    });

    await page.goto("/");
    await page.click(".ftp-source-item button:has-text('Подключиться')");
    const wv = page.locator(".ftp-source-item").getByRole("button", { name: "WebView" });
    await wv.click(); // обычный клик → окно приложения
    await wv.click({ modifiers: ["Control"] }); // Ctrl+клик → браузер

    await expect
      .poll(() => page.evaluate(() => window.__calls), { timeout: 15000 })
      .toEqual([
        ["window", "http://192.168.1.88/app/dashboard"],
        ["browser", "http://192.168.1.88/app/dashboard"],
      ]);
  });
});
