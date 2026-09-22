import { expect, test } from "@playwright/test";
import { ensureAnalysis, openWashList } from "./helpers.js";

// Названия программ мойки: вкладка редактора, области и то, что переименование
// доезжает до журнала. Проверяем именно сквозь UI — наследование считает сервер,
// и расхождение «в редакторе одно, в списке другое» иначе не поймать.
//
// В фикстурах: канал 1 — объект 3 (программа 1) и объект 4 (программа 2);
// канал 2 — объект 5 (программы 1 и 3) и объект 6 (программа 2).
const SCOPES = ["*", "2", "2:5"];

async function openProgramsTab(page) {
  await page.locator("#openObjectEditor").click();
  await page.locator('[data-editor-tab="programs"]').click();
  await expect(page.locator("#programEditorList [data-program-editor-form]").first()).toBeVisible();
}

// Первым [data-close-object-editor] в разметке идёт подложка — она под
// панелью, и клик по ней перехватывается. Закрываем кнопкой в шапке.
function closeEditor(page) {
  return page.locator(".object-editor-header-actions [data-close-object-editor]").click();
}

function programRow(page, programId) {
  return page.locator("#programEditorList [data-program-editor-form]").filter({
    has: page.locator(`input[name="program_id"][value="${programId}"]`),
  });
}

test.beforeEach(async ({ page }) => {
  await ensureAnalysis(page);
  for (const scope of SCOPES) {
    await page.request.post("/api/program-name", { data: { scope, mode: "reset_scope" } });
  }
});

test("вкладка показывает семь программ панели", async ({ page }) => {
  await openWashList(page);
  await openProgramsTab(page);

  await expect(page.locator("#programEditorList [data-program-editor-form]")).toHaveCount(7);
  // Поле пустое, а встроенное название — в placeholder: видно, что наследуется.
  const input = programRow(page, 1).locator('input[name="program_name"]');
  await expect(input).toHaveValue("");
  await expect(input).toHaveAttribute("placeholder", "Ополаскивание вторичной водой");
  // Кнопка сброса без собственного названия бессмысленна.
  await expect(programRow(page, 1).locator("[data-program-editor-reset]")).toBeDisabled();
});

test("переименование в области «Все объекты» доезжает до журнала", async ({ page }) => {
  await openWashList(page);
  const journalPrograms = page.locator("#washList [data-key] .wash-entry-program");
  await expect(journalPrograms.filter({ hasText: "Мойка щелочью и кислотой" }).first()).toBeVisible();

  await openProgramsTab(page);
  await programRow(page, 3).locator('input[name="program_name"]').fill("Щёлочь + кислота");
  await programRow(page, 3).locator('button[type="submit"]').click();
  await expect(programRow(page, 3).locator(".program-row-mark--own")).toHaveText("своё");

  await closeEditor(page);
  await expect(journalPrograms.filter({ hasText: "Щёлочь + кислота" }).first()).toBeVisible();
  await expect(journalPrograms.filter({ hasText: "Мойка щелочью и кислотой" })).toHaveCount(0);
});

test("область объекта перебивает общую, сброс возвращает наследование", async ({ page }) => {
  await openWashList(page);
  await openProgramsTab(page);

  const inheritedMark = () => programRow(page, 3).locator(".program-row-mark").first();

  await programRow(page, 3).locator('input[name="program_name"]').fill("Общее название");
  await programRow(page, 3).locator('button[type="submit"]').click();
  await expect(programRow(page, 3).locator(".program-row-mark--own")).toBeVisible();

  // Объект 5 канала 2 — единственный, где программа 3 реально встречается.
  await page.locator('[data-program-scope="2:5"]').click();
  await expect(programRow(page, 3).locator('input[name="program_name"]')).toHaveValue("");
  await expect(inheritedMark()).toHaveText("наследует: Общее название");

  await programRow(page, 3).locator('input[name="program_name"]').fill("Только этот объект");
  await programRow(page, 3).locator('button[type="submit"]').click();
  await expect(programRow(page, 3).locator(".program-row-mark--own")).toBeVisible();

  await closeEditor(page);
  const journalPrograms = page.locator("#washList [data-key] .wash-entry-program");
  await expect(journalPrograms.filter({ hasText: "Только этот объект" }).first()).toBeVisible();

  await openProgramsTab(page);
  await page.locator('[data-program-scope="2:5"]').click();
  await programRow(page, 3).locator("[data-program-editor-reset]").click();
  await expect(inheritedMark()).toHaveText("наследует: Общее название");
});

test("пустое поле снимает собственное название области", async ({ page }) => {
  await openWashList(page);
  await openProgramsTab(page);

  await programRow(page, 2).locator('input[name="program_name"]').fill("Своё");
  await programRow(page, 2).locator('button[type="submit"]').click();
  await expect(programRow(page, 2).locator(".program-row-mark--own")).toBeVisible();

  // Пустое поле — отказ от своего названия, а не попытка сохранить пустоту.
  await programRow(page, 2).locator('input[name="program_name"]').fill("");
  await programRow(page, 2).locator('button[type="submit"]').click();
  await expect(programRow(page, 2).locator(".program-row-mark").first()).toHaveText(
    "наследует: Ополаскивание чистой водой"
  );
});
