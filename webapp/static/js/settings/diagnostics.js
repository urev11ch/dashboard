// Раздел «Диагностика» в настройках: снимок состояния источника данных.

import { apiJson } from "../core/api.js";
import { formatBytes, formatDateTime } from "../core/format.js";

const { escapeHtml } = window.WashChart;

function fetchDiagnostics() {
  return apiJson("/api/diagnostics");
}

// Строки диагностики (снимок состояния источника) для раздела в настройках.
export async function loadDiagnosticsInto(container) {
  if (!container) {
    return;
  }
  let data = null;
  try {
    data = await fetchDiagnostics();
  } catch (_error) {
    container.innerHTML = '<div class="technical-empty">Диагностика недоступна.</div>';
    return;
  }
  const kindLabel = { ftp: "FTP", folder: "Папка", none: "—" };
  const counts = data?.counts || {};
  const rows = data
    ? [
        ["Источник", kindLabel[data.source_kind] || "—"],
        ["Путь", data.display_root || "—"],
        ["Последняя синхронизация", formatDateTime(data.last_sync) || "—"],
        ["Моек", counts.cycles ?? 0],
        ["Объектов", counts.objects ?? 0],
        ["Баз данных", counts.databases ?? 0],
        ["Архивов", counts.archives ?? 0],
        ["FTP-панелей", counts.ftp_sources ?? 0],
        [
          "Автообновление",
          data.auto_refresh?.enabled ? `вкл · ${data.auto_refresh.minutes} мин` : "выкл",
        ],
        ["Объём datalog", formatBytes(data.datalog?.size_bytes || 0)],
        [
          "Хранение архивов",
          data.datalog?.retention_enabled ? `${data.datalog.retention_days} дней` : "выкл",
        ],
        ["Последняя очистка", formatDateTime(data.datalog?.last_cleanup) || "—"],
        ["Обработка", data.job?.active ? data.job.message || "выполняется" : "нет"],
        ["Ошибка", data.error || "—"],
      ]
    : [["Диагностика", "недоступна"]];
  container.innerHTML = rows
    .map(
      ([key, value]) => `
        <div class="diagnostics-row">
          <span class="diagnostics-key">${escapeHtml(key)}</span>
          <span class="diagnostics-value">${escapeHtml(String(value))}</span>
        </div>
      `
    )
    .join("");
}
