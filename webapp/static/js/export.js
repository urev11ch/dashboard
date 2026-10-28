// Экспорт: печать и PDF отчёта по мойке (через общий printRoot) и выгрузка
// журнала в XLSX. В десктопе — через мост pywebview, в браузере — скачиванием.

import { fetchWithTimeout } from "./core/api.js";
import { formatConcentration, formatDateTime } from "./core/format.js";
import { showToast } from "./core/toast.js";
import { getDetail } from "./workspace/cache.js";
import { state } from "./workspace/store.js";
import { getFilteredRows } from "./workspace/wash-list.js";

const { escapeHtml } = window.WashChart;

// Зависимости от main.js: printRoot (создаётся там вместе с остальными корнями
// окон) и mountChart из wash-modal.js (через main.js — wash-modal сам импортирует
// этот модуль, прямой импорт дал бы цикл). Задаются в initExport.
let printRoot = null;
let mountChart = null;

export function initExport(root, deps) {
  printRoot = root;
  ({
    mountChart,
  } = deps);
}

// Заголовок окна на время печати: печать подставляет его как имя PDF.
let restoreDocumentTitle = null;

function buildPrintFileName(detail) {
  const safeParts = [detail.object_name, detail.program, detail.start_time || detail.date_time]
    .filter(Boolean)
    .map((value) =>
      String(value)
        .trim()
        .replaceAll(/[\\/:*?"<>|]+/g, "-")
        .replaceAll(/\s+/g, "_")
    );

  return safeParts.length ? safeParts.join("__") : `wash_cycle_${Date.now()}`;
}

// Пары [подпись, значение] для оценённых фаз концентрации (unknown пропускаем).
// Используется и в окне деталей, и в печатном отчёте — вид совпадает.
// Датчик на возврате: оцениваем рабочий участок (полку). «Не достигла нормы» —
// концентрация ни разу не вышла на уровень (показываем пик); «провал» — вышла,
// но просела на рабочем участке; «в норме» — вышла и держалась (мин полки).
export function concentrationSummaryRows(detail) {
  const phases = Array.isArray(detail.concentration_eval) ? detail.concentration_eval : [];
  return phases
    .filter((phase) => phase && phase.status !== "unknown")
    .map((phase) => {
      const norm = `норма ${formatConcentration(phase.norm)}`;
      let value;
      if (phase.status === "low" && phase.reason === "not_reached") {
        value = `макс ${formatConcentration(phase.peak)} / ${norm} — не достигла нормы`;
      } else if (phase.status === "low") {
        value = `мин на режиме ${formatConcentration(phase.floor)} / ${norm} — провал ниже нормы`;
      } else {
        value = `мин на режиме ${formatConcentration(phase.floor)} / ${norm} — в норме`;
      }
      return [`Концентрация: ${phase.label}`, value];
    });
}

function renderPrintSummaryRows(detail) {
  return [
    ["Объект", detail.object_name],
    ["Программа мойки", detail.program],
    ["Начало мойки", formatDateTime(detail.start_time || detail.date_time)],
    ["Конец мойки", formatDateTime(detail.end_time)],
    ["Длительность мойки", detail.duration],
    ["Результат", detail.status],
    ...concentrationSummaryRows(detail),
  ]
    .map(
      ([label, value]) => `
        <tr${label === "Результат" ? " data-wash-result-row" : ""}>
          <th scope="row">${escapeHtml(label)}</th>
          <td>${escapeHtml(String(value ?? "").trim() || "—")}</td>
        </tr>
      `
    )
    .join("");
}

// Печатный отчёт повторяет вид окна просмотра графика мойки:
// та же карточка-сводка (chart-modal-summary-card + chart-modal-table)
// и та же рамка графика, только без кнопок и навигации.
function renderPrintDocument(detail) {
  return `
    <article class="chart-print-document" aria-label="Отчет по мойке">
      <div class="chart-modal-summary-card chart-print-summary-card">
        <table class="chart-modal-table" aria-label="Параметры мойки">
          <tbody>
            ${renderPrintSummaryRows(detail)}
          </tbody>
        </table>
      </div>
      <section class="chart-print-chart" aria-label="График мойки">
        <div class="chart-host chart-host--modal chart-host--print" data-chart-host></div>
        <div class="technical-empty chart-empty" data-chart-empty hidden>
          Подготавливаю данные графика.
        </div>
      </section>
    </article>
  `;
}

export function clearPrintMode() {
  document.body.classList.remove("modal-print-mode");
  if (restoreDocumentTitle) {
    document.title = restoreDocumentTitle;
    restoreDocumentTitle = null;
  }
}

export function clearDetachedPrintDocument() {
  printRoot.hidden = true;
  printRoot.innerHTML = "";
}

function enterExportMode() {
  document.body.classList.add("modal-export-mode");
}

function clearExportMode() {
  document.body.classList.remove("modal-export-mode");
}

function waitForNextFrame() {
  return new Promise((resolve) => window.requestAnimationFrame(() => resolve()));
}

async function prepareDetachedPrintDocument(detail) {
  printRoot.hidden = false;
  printRoot.innerHTML = renderPrintDocument(detail);

  try {
    await mountChart(printRoot, detail, null);
  } catch (_error) {
    // The print document keeps its empty state if chart rendering fails.
  }

  await waitForNextFrame();
  await waitForNextFrame();
}

async function withExportMode(action) {
  const scrollX = window.scrollX;
  const scrollY = window.scrollY;
  enterExportMode();
  window.scrollTo(0, 0);
  await waitForNextFrame();
  await waitForNextFrame();

  try {
    return await action();
  } finally {
    clearExportMode();
    window.scrollTo(scrollX, scrollY);
  }
}

function hasDesktopExportApi() {
  return typeof window.pywebview?.api?.save_wash_export === "function";
}

// Имя файла задаёт сервер (Content-Disposition, filename* в UTF-8) — здесь
// только достаём его; без заголовка берём нейтральное.
function parseAttachmentFileName(header) {
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header || "");
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1].trim());
    } catch (_error) {
      // Битое процентное кодирование — уходим на дефолт ниже.
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header || "");
  return plain ? plain[1].trim() : "Мойки.xlsx";
}

async function blobToBase64(blob) {
  const buffer = await blob.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  // По кускам: btoa(String.fromCharCode(...bytes)) на файле в мегабайты
  // разворачивает массив в аргументы и падает по лимиту стека.
  let binary = "";
  const CHUNK = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += CHUNK) {
    binary += String.fromCharCode.apply(null, bytes.subarray(offset, offset + CHUNK));
  }
  return btoa(binary);
}

function downloadBlobInBrowser(blob, fileName) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.append(link);
  link.click();
  link.remove();
  // Освобождаем URL после клика: в Chrome/WebView2 отзыв в том же кадре
  // иногда отменяет уже начатую загрузку.
  window.setTimeout(() => URL.revokeObjectURL(url), 10000);
}

let exportJobInFlight = false;

export async function exportWashesXlsx(button) {
  if (exportJobInFlight) {
    showToast("Выгрузка уже выполняется, дождитесь завершения.", "info");
    return;
  }

  const rows = state.filteredRows.length ? state.filteredRows : getFilteredRows();
  const keys = rows.map((row) => row.key).filter(Boolean);
  if (!keys.length) {
    showToast("Нет моек для выгрузки — измените фильтры.", "info");
    return;
  }

  exportJobInFlight = true;
  const originalLabel = button?.textContent || "Выгрузить XLSX";
  if (button) {
    button.disabled = true;
    button.textContent = "Готовлю...";
  }

  try {
    const response = await fetchWithTimeout("/api/wash-export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keys }),
      // Каждая строка читает сэмплы своей мойки: на сотнях моек это заметно
      // дольше обычного запроса, но ждать бесконечно тоже нельзя.
      timeout: 180000,
    });

    if (!response.ok) {
      let detail = "";
      try {
        detail = (await response.json())?.detail || "";
      } catch (_error) {
        detail = "";
      }
      throw new Error(detail || "Не удалось подготовить файл.");
    }

    const missing = Number(response.headers.get("X-Export-Missing") || 0);
    const fileName = parseAttachmentFileName(response.headers.get("Content-Disposition"));
    const blob = await response.blob();

    if (hasDesktopExportApi()) {
      const result = await window.pywebview.api.save_wash_export({
        file_name: fileName,
        content_base64: await blobToBase64(blob),
      });
      if (result?.cancelled) {
        showToast("Выгрузка отменена", "info");
        return;
      }
      if (!result?.ok) {
        throw new Error("desktop-export-save-failed");
      }
    } else {
      downloadBlobInBrowser(blob, fileName);
    }

    showToast(`Выгружено моек: ${keys.length - missing}`, "success");
    if (missing > 0) {
      // Ключи, которых уже нет в анализе: список открыли до обновления
      // источника, а выгрузили после.
      showToast(`Пропущено моек (список устарел): ${missing}`, "info");
    }
  } catch (error) {
    const message = error?.message && !/^desktop-/.test(error.message)
      ? error.message
      : "Не удалось выгрузить мойки. Попробуйте ещё раз.";
    showToast(message, "error");
  } finally {
    exportJobInFlight = false;
    if (button) {
      button.disabled = false;
      button.textContent = originalLabel;
    }
  }
}

function hasDesktopPdfApi() {
  return typeof window.pywebview?.api?.save_graph_pdf === "function";
}

// printRoot — единственный общий узел печатного документа. Любой экспорт
// (PDF строки журнала, PDF/печать из модалки) перезаписывает его целиком, а в
// finally очищает. Без общей блокировки два параллельных экспорта (клик по PDF
// строки A, затем B; или строка + модалка) затирали документ друг друга —
// получался PDF не той мойки либо пустой. Блокировка одна на весь printRoot,
// а не по кнопке.
let printJobInFlight = false;

async function runExclusivePrintJob(job) {
  if (printJobInFlight) {
    showToast("Экспорт уже выполняется, дождитесь завершения.", "info");
    return { skipped: true };
  }
  printJobInFlight = true;
  try {
    return await job();
  } finally {
    printJobInFlight = false;
  }
}

export function saveGraphPdf(detail, button) {
  return runExclusivePrintJob(() => saveGraphPdfInner(detail, button));
}

async function saveGraphPdfInner(detail, button) {
  let cleanupDetachedDocument = true;
  const originalLabel = button?.textContent || "Сохранить как PDF";

  if (button) {
    button.disabled = true;
    button.textContent = "Сохраняю PDF...";
  }

  try {
    if (!hasDesktopPdfApi()) {
      cleanupDetachedDocument = false;
      await printReportDocumentInner(detail, "pdf");
      return;
    }

    await prepareDetachedPrintDocument(detail);

    const response = await withExportMode(() =>
      window.pywebview.api.save_graph_pdf({
        file_name: `${buildPrintFileName(detail)}.pdf`,
      })
    );

    if (response?.unsupported) {
      cleanupDetachedDocument = false;
      await printReportDocumentInner(detail, "pdf");
      return;
    }

    if (!response?.ok && !response?.cancelled) {
      throw new Error("desktop-pdf-save-failed");
    }
    if (response?.ok) {
      showToast("PDF сохранён", "success");
    } else if (response?.cancelled) {
      showToast("Сохранение PDF отменено", "info");
    }
  } catch (_error) {
    showToast("Не удалось сохранить PDF. Попробуйте ещё раз.", "error");
  } finally {
    if (cleanupDetachedDocument) {
      clearDetachedPrintDocument();
    }
    if (button) {
      button.disabled = false;
      button.textContent = originalLabel;
    }
  }
}

export function printReportDocument(detail, intent = "print") {
  return runExclusivePrintJob(() => printReportDocumentInner(detail, intent));
}

async function printReportDocumentInner(detail, intent = "print") {
  await prepareDetachedPrintDocument(detail);

  const originalTitle = document.title;
  if (intent === "pdf" && detail) {
    document.title = `${buildPrintFileName(detail)}.pdf`;
  }
  restoreDocumentTitle = originalTitle;

  document.body.classList.add("modal-print-mode");
  await waitForNextFrame();
  await waitForNextFrame();
  try {
    window.print();
  } catch (error) {
    // WebView без поддержки печати: снимаем печатный режим сразу, иначе body
    // залипнет в печатной вёрстке (afterprint не сработает — диалог не открылся).
    clearPrintMode();
    clearDetachedPrintDocument();
    throw error;
  }
  window.setTimeout(() => {
    clearPrintMode();
    clearDetachedPrintDocument();
  }, 1000);
}

export async function saveWashRowPdf(key, button) {
  // Блокируем кнопку ДО getDetail (реальный сетевой запрос): иначе двойной клик
  // до его резолва запускал бы два saveGraphPdf на общий printRoot, перетирая
  // печатный документ друг друга. saveGraphPdf блокирует кнопку только у себя —
  // уже после загрузки детали.
  if (button?.disabled) {
    return;
  }
  if (button) {
    button.disabled = true;
  }
  try {
    const detail = await getDetail(key);
    await saveGraphPdf(detail, button);
  } catch (_error) {
    showToast("Не удалось сохранить PDF. Попробуйте ещё раз.", "error");
  } finally {
    if (button) {
      button.disabled = false;
    }
  }
}
