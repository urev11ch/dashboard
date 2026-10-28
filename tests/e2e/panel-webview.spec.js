import { test, expect } from "@playwright/test";

// Флоу панели: поиск → «Добавить панель» (сохранение) → карточка панели на
// главной (журнал, WebView, меню «⋯»). Обнаружение мокаем.
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

test("«Добавить вручную» — спойлер: раскрывает и сворачивает форму", async ({ page }) => {
  await page.goto("/");
  // Панель за маршрутизатором скан не найдёт никогда, поэтому ручной путь
  // открыт с самого начала, а не только после неудачного поиска.
  const manual = page.locator("[data-ftp-manual]");
  const form = page.locator("[data-ftp-add]");
  await expect(manual).toBeVisible();
  await expect(form).toBeHidden();
  await expect(manual).toHaveAttribute("aria-expanded", "false");

  await manual.click();
  await expect(form).toBeVisible();
  await expect(manual).toHaveAttribute("aria-expanded", "true");
  await expect(form.locator("button[type=submit]")).toHaveText("Добавить панель");

  // Тем же нажатием сворачивается обратно — иначе убрать поля с экрана нечем.
  await manual.click();
  await expect(form).toBeHidden();
  await expect(manual).toHaveAttribute("aria-expanded", "false");
});

test("пустой скан не прячет ручное подключение", async ({ page }) => {
  // Роут, зарегистрированный позже beforeEach, побеждает — отдаём пустой список.
  await page.route("**/api/ftp/discover", (route) =>
    route.fulfill({
      json: { scanned: 253, ftp_hosts: 0, network: "192.168.1.0/24", panels: [] },
    }),
  );
  await page.goto("/");
  await page.click("[data-ftp-discover]");
  await expect(page.locator("[data-ftp-discover-status]")).toContainText("Панели не найдены", {
    timeout: 15000,
  });
  await expect(page.locator("[data-ftp-manual]")).toBeVisible();
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
        equipment: "cip",
      },
    });
  });
  test.afterEach(async ({ page }) => {
    await page.request.post("/workspace/reset");
    await page.goto("/?view=menu");
    const ids = await page
      .locator('[data-source-card][data-kind="ftp"]')
      .evaluateAll((cards) => cards.map((card) => card.dataset.id));
    for (const id of ids) {
      await page.request.post("/workspace/source/delete", {
        form: { source_kind: "ftp", source_id: id },
      });
    }
  });

  const panelCard = (page) =>
    page.locator('[data-source-card][data-kind="ftp"]', { hasText: "192.168.1.88" });

  test("карточка панели: «Открыть журнал моек» и WebView, без шага «Подключиться»", async ({
    page,
  }) => {
    await page.goto("/?view=menu");
    await page.click('[data-home-mode="cip"]');
    const card = panelCard(page);
    await expect(card).toBeVisible();
    await expect(card.getByRole("button", { name: /Открыть журнал моек/ })).toBeVisible();
    await expect(card.getByRole("button", { name: "WebView" })).toBeVisible();
    await expect(card.getByRole("button", { name: "Подключиться" })).toHaveCount(0);
    await expect(card.locator(".source-card-address")).toContainText("192.168.1.88");
  });

  test("«Переименовать» в меню карточки меняет название", async ({ page }) => {
    await page.goto("/?view=menu");
    await page.click('[data-home-mode="cip"]');
    const card = panelCard(page);
    await card.locator("[data-source-more] > summary").click();
    await card.locator("[data-source-rename]").click();
    const modal = page.locator(".ftp-connect-modal");
    await expect(modal).toBeVisible();
    await modal.locator('input[name="label"]').fill("Цех 5");
    await modal.getByRole("button", { name: "Сохранить" }).click();
    await page.waitForLoadState("networkidle");
    await expect(panelCard(page).locator(".source-card-name")).toHaveText("Цех 5");
  });

  test("WebView панели открывает окно/вкладку /app/dashboard", async ({ page }) => {
    // В вебе WebView открывается через window.open (топ-левел), не iframe:
    // EasyWeb запрещает встраивание (X-Frame-Options). Перехватываем open.
    await page.addInitScript(() => {
      window.__opened = [];
      window.open = (u) => {
        window.__opened.push(u);
        return null;
      };
    });

    // goto ждёт события load — обработчики модулей уже навешены.
    await page.goto("/?view=menu");
    await page.click('[data-home-mode="cip"]');
    await panelCard(page).getByRole("button", { name: "WebView" }).click();

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

    await page.goto("/?view=menu");
    await page.click('[data-home-mode="cip"]');
    const wv = panelCard(page).getByRole("button", { name: "WebView" });
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
