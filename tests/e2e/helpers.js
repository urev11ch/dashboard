import path from "node:path";
import { expect } from "@playwright/test";

// Гарантирует, что сервер проанализировал папку-фикстуру с тестовыми .db и в
// списке есть мойки. Фикстура лежит в репозитории (datalog/ в .gitignore и в CI
// отсутствует). Анализ триггерим POST-ом /workspace/open, ждём завершения джоба.
export async function ensureAnalysis(page) {
  const fixtures = path.resolve(process.cwd(), "tests/e2e/fixtures");
  const already = await page.request.get("/api/workspace-data");
  const data = await already.json();
  if (data.has_analysis && data.summary?.cycle_count > 0) {
    return;
  }

  await page.request.post("/workspace/open", {
    form: { path: fixtures },
  });

  for (let i = 0; i < 40; i += 1) {
    const res = await page.request.get("/api/workspace-job");
    const job = await res.json();
    if (job.active === false) break;
    await page.waitForTimeout(300);
  }
}

// Открывает журнал моек с включённым периодом «Весь период».
//
// По умолчанию журнал показывает мойки за последние 7 дней
// (DEFAULT_PERIOD_PRESET = "7d"), а .db-фикстуры имеют ФИКСИРОВАННУЮ дату и со
// временем выпадают из этого окна — из-за чего список становился пустым и тесты
// падали «через неделю после фиксации фикстур» (данные с бэкенда есть, но
// клиентский фильтр их прячет). Переключение на «Все» делает тесты
// независимыми от текущей даты.
export async function openWashList(page) {
  await page.goto("/");
  await page.locator('[data-period-preset="all"]').click();
  await expect(page.locator("#washList [data-key]").first()).toBeVisible({ timeout: 15000 });
}
