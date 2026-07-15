import path from "node:path";

// Гарантирует, что сервер проанализировал встроенную папку datalog и в списке
// есть мойки. Анализ триггерим POST-ом /workspace/open, затем ждём завершения
// джоба через /api/workspace-job.
export async function ensureAnalysis(page) {
  const datalog = path.resolve(process.cwd(), "datalog");
  const already = await page.request.get("/api/workspace-data");
  const data = await already.json();
  if (data.has_analysis && data.summary?.cycle_count > 0) {
    return;
  }

  await page.request.post("/workspace/open", {
    form: { path: datalog },
  });

  for (let i = 0; i < 40; i += 1) {
    const res = await page.request.get("/api/workspace-job");
    const job = await res.json();
    if (job.active === false) break;
    await page.waitForTimeout(300);
  }
}
