import path from "node:path";
import { test, expect } from "@playwright/test";

// Главная: режим CIP | ПОУ, источники-карточки, экран ПОУ и уведомление о
// несовпадении типа архива. Фикстура ПОУ — tests/e2e/fixtures-pou (теги как на
// реальной панели: процесс, шаг, клапан возврата, две температуры пастеризации,
// температура продукта на выходе).
const POU_FIXTURES = path.resolve(process.cwd(), "tests/e2e/fixtures-pou");

async function waitJob(page) {
  for (let i = 0; i < 60; i += 1) {
    const job = await (await page.request.get("/api/workspace-job")).json();
    if (job.active === false) {
      return job;
    }
    await page.waitForTimeout(300);
  }
  throw new Error("Обработка источника не завершилась");
}

async function openFolder(page, equipment) {
  await page.request.post("/workspace/open", { form: { path: POU_FIXTURES, equipment } });
  return waitJob(page);
}

test.beforeEach(async ({ page }) => {
  await page.request.post("/workspace/reset");
});

test.describe("режим главной", () => {
  test.beforeEach(async ({ page }) => {
    for (const [host, label, equipment] of [
      ["10.0.0.11", "E2E мойка", "cip"],
      ["10.0.0.12", "E2E пастеризатор", "pou"],
    ]) {
      await page.request.post("/workspace/ftp-source/add", {
        form: { host, port: "21", password: "1", path: "/datalog", passive: "on", label, equipment },
      });
    }
  });
  test.afterEach(async ({ page }) => {
    await page.goto("/?view=menu");
    const ids = await page
      .locator('[data-source-card][data-kind="ftp"]')
      .evaluateAll((cards) =>
        cards.filter((card) => card.textContent.includes("E2E")).map((card) => card.dataset.id)
      );
    for (const id of ids) {
      await page.request.post("/workspace/source/delete", { form: { source_kind: "ftp", source_id: id } });
    }
  });

  test("переключатель показывает источники своего типа и запоминается", async ({ page }) => {
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    await page.goto("/?view=menu");
    const cip = page.locator("[data-source-card]", { hasText: "E2E мойка" });
    const pou = page.locator("[data-source-card]", { hasText: "E2E пастеризатор" });

    await page.click('[data-home-mode="cip"]');
    await expect(cip).toBeVisible();
    await expect(pou).toBeHidden();
    await expect(cip.getByRole("button", { name: /Открыть журнал моек/ })).toBeVisible();

    await page.click('[data-home-mode="pou"]');
    await expect(pou).toBeVisible();
    await expect(cip).toBeHidden();
    await expect(pou.getByRole("button", { name: /Открыть тренды/ })).toBeVisible();

    await page.reload();
    await expect(pou).toBeVisible();
    await expect(cip).toBeHidden();
    expect(errors).toEqual([]);
  });

  test("«Перенести в ПОУ» меняет тип источника", async ({ page }) => {
    await page.goto("/?view=menu");
    await page.click('[data-home-mode="cip"]');
    const card = page.locator("[data-source-card]", { hasText: "E2E мойка" });
    await card.locator("[data-source-more] > summary").click();
    await card.getByRole("button", { name: "Перенести в ПОУ" }).click();
    await page.waitForLoadState("networkidle");

    await page.click('[data-home-mode="pou"]');
    await expect(page.locator("[data-source-card]", { hasText: "E2E мойка" })).toBeVisible();
  });
});

test("папка ПОУ открывается экраном трендов", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const job = await openFolder(page, "pou");
  expect(job.status).toBe("completed");

  await page.goto("/");
  await expect(page.locator("[data-pou-screen]")).toBeVisible();
  await expect(page.locator("[data-pou-stats]")).toContainText("Тегов: 6");
  await expect(page.locator("[data-pou-screen] .wash-chart-svg")).toBeVisible({ timeout: 15000 });
  await expect(page.locator("[data-pou-range]")).toContainText("14.07.2026");
  // Шесть тегов на трёх панелях: температура, клапан возврата, процесс и шаг.
  await expect(page.locator("[data-pou-screen] .wash-chart-legend-item")).toHaveCount(6);
  expect(errors).toEqual([]);
});

test("архив ПОУ, открытый как CIP: уведомление и переход в режим ПОУ", async ({ page }) => {
  const job = await openFolder(page, "cip");
  expect(job.status).toBe("failed");

  await page.goto("/?view=menu");
  const notice = page.locator("[data-equipment-mismatch]");
  await expect(notice).toContainText("В источнике нет архивов CIP, но найдены архивы ПОУ (1)");
  await notice.getByRole("button", { name: "Открыть в режиме ПОУ" }).click();
  await waitJob(page);

  await page.goto("/");
  await expect(page.locator("[data-pou-screen]")).toBeVisible();
});
