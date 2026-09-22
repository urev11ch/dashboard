import { expect, test } from "@playwright/test";
import { ensureAnalysis, openWashList } from "./helpers.js";

// Названия программ мойки: вкладка редактора и то, что переименование доезжает
// до журнала. Проверяем сквозь UI — расхождение «в редакторе одно, в списке
// другое» иначе не поймать.
//
// В фикстурах встречаются программы 1, 2 и 3.

// Первым [data-close-object-editor] в разметке идёт подложка — она под панелью,
// и клик по ней перехватывается. Закрываем кнопкой в шапке.
function closeEditor(page) {
  return page.locator(".object-editor-header-actions [data-close-object-editor]").click();
}

async function openProgramsTab(page) {
  await page.locator("#openObjectEditor").click();
  await page.locator('[data-editor-tab="programs"]').click();
  await expect(page.locator("#programEditorList [data-program-editor-form]").first()).toBeVisible();
}

function programRow(page, programId) {
  return page.locator("#programEditorList [data-program-editor-form]").filter({
    has: page.locator(`input[name="program_id"][value="${programId}"]`),
  });
}

test.beforeEach(async ({ page }) => {
  await ensureAnalysis(page);
  for (let programId = 1; programId <= 7; programId += 1) {
    await page.request.post("/api/program-name", {
      data: { program_id: programId, mode: "reset" },
    });
  }
});

test("вкладка показывает семь программ панели", async ({ page }) => {
  await openWashList(page);
  await openProgramsTab(page);

  await expect(page.locator("#programEditorList [data-program-editor-form]")).toHaveCount(7);
  // Поле пустое, а встроенное название — в placeholder: видно, что вернёт сброс.
  const input = programRow(page, 1).locator('input[name="program_name"]');
  await expect(input).toHaveValue("");
  await expect(input).toHaveAttribute("placeholder", "Ополаскивание вторичной водой");
  // Кнопка сброса без своего названия бессмысленна.
  await expect(programRow(page, 1).locator("[data-program-editor-reset]")).toBeDisabled();
});

test("переименование доезжает до журнала и действует на все объекты", async ({ page }) => {
  await openWashList(page);
  const journalPrograms = page.locator("#washList [data-key] .wash-entry-program");
  const before = await journalPrograms.filter({ hasText: "Ополаскивание вторичной водой" }).count();
  expect(before).toBeGreaterThan(1);

  await openProgramsTab(page);
  await programRow(page, 1).locator('input[name="program_name"]').fill("Ополаскивание ВВ");
  await programRow(page, 1).locator('button[type="submit"]').click();
  await expect(programRow(page, 1).locator(".program-row-mark--own")).toHaveText("своё");

  await closeEditor(page);
  // Название общее: переименовались мойки всех объектов, где шла эта программа.
  await expect(journalPrograms.filter({ hasText: "Ополаскивание ВВ" })).toHaveCount(before);
  await expect(journalPrograms.filter({ hasText: "Ополаскивание вторичной водой" })).toHaveCount(0);
});

test("сброс возвращает встроенное название", async ({ page }) => {
  await openWashList(page);
  await openProgramsTab(page);

  await programRow(page, 3).locator('input[name="program_name"]').fill("Щёлочь + кислота");
  await programRow(page, 3).locator('button[type="submit"]').click();
  await expect(programRow(page, 3).locator(".program-row-mark--own")).toBeVisible();

  await programRow(page, 3).locator("[data-program-editor-reset]").click();
  await expect(programRow(page, 3).locator(".program-row-mark--own")).toHaveCount(0);
  await expect(programRow(page, 3).locator('input[name="program_name"]')).toHaveValue("");

  await closeEditor(page);
  await expect(
    page.locator("#washList [data-key] .wash-entry-program").filter({ hasText: "Мойка щелочью и кислотой" }).first()
  ).toBeVisible();
});

test("пустое поле снимает своё название", async ({ page }) => {
  await openWashList(page);
  await openProgramsTab(page);

  await programRow(page, 2).locator('input[name="program_name"]').fill("Своё");
  await programRow(page, 2).locator('button[type="submit"]').click();
  await expect(programRow(page, 2).locator(".program-row-mark--own")).toBeVisible();

  // Пустое поле — отказ от своего названия, а не попытка сохранить пустоту.
  await programRow(page, 2).locator('input[name="program_name"]').fill("");
  await programRow(page, 2).locator('button[type="submit"]').click();
  await expect(programRow(page, 2).locator(".program-row-mark--own")).toHaveCount(0);
  await expect(programRow(page, 2).locator('input[name="program_name"]')).toHaveAttribute(
    "placeholder",
    "Ополаскивание чистой водой"
  );
});
