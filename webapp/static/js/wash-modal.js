// Окно мойки: сводка, параметры и график (WashChart), переход к соседним мойкам,
// печать/PDF. modalRequestId — токен открытого окна: ответы для закрытого или
// уже сменённого окна не применяются.

import { refreshWindowMaximizedState } from "./core/desktop.js";
import { badgeClass, formatDateTime } from "./core/format.js";
import { syncOverlayState } from "./core/overlays.js";
import { runHandler } from "./core/toast.js";
import {
  clearDetachedPrintDocument,
  clearPrintMode,
  concentrationSummaryRows,
  printReportDocument,
  saveGraphPdf,
} from "./export.js";
import {
  getChartPayload,
  getDetail,
  prefetchChartPayload,
  prefetchWashContext,
} from "./workspace/cache.js";
import { getModalNavigation } from "./workspace/wash-list.js";

const { escapeHtml } = window.WashChart;

// Корневой элемент окна создаёт main.js (порядок окон в DOM задаёт наложение).
let modalRoot = null;

export function initWashModal(root) {
  modalRoot = root;
}

let modalRequestId = 0;

export async function mountChart(root, detail, requestId = modalRequestId) {
  const hosts = Array.from(root.querySelectorAll("[data-chart-host]"));
  const emptyStates = Array.from(root.querySelectorAll("[data-chart-empty]"));

  const setEmptyState = (visible, message = "") => {
    emptyStates.forEach((emptyState) => {
      emptyState.hidden = !visible;
      if (message) {
        emptyState.textContent = message;
      }
    });
  };

  if (!hosts.length || !detail?.chart_data_url || !window.WashChart?.mount) {
    hosts.forEach((host) => {
      host.hidden = true;
    });
    setEmptyState(true);
    return false;
  }

  hosts.forEach((host) => {
    host.hidden = false;
  });
  setEmptyState(false);

  try {
    const [payload] = await Promise.all([
      getChartPayload(detail.chart_data_url),
      window.WashChart.hydrate ? window.WashChart.hydrate() : Promise.resolve(),
    ]);

    if (requestId !== null && requestId !== modalRequestId) {
      return false;
    }

    let renderedCount = 0;
    hosts.forEach((host) => {
      if (!host.isConnected) {
        return;
      }
      const rendered = window.WashChart.mount(host, payload);
      host.hidden = !rendered;
      if (rendered) {
        renderedCount += 1;
      }
    });

    if (!renderedCount) {
      setEmptyState(true, "Для этой мойки не найдено данных для построения графика.");
    }
    return renderedCount > 0;
  } catch (_error) {
    hosts.forEach((host) => {
      host.hidden = true;
    });
    setEmptyState(true, "Не удалось загрузить данные графика.");
    return false;
  }
}

export function closeChartModal() {
  modalRequestId += 1;
  modalRoot.hidden = true;
  modalRoot.innerHTML = "";
  clearPrintMode();
  clearDetachedPrintDocument();
  syncOverlayState();
}

function renderModalLoading() {
  modalRoot.hidden = false;
  syncOverlayState();
  modalRoot.innerHTML = `
    <div class="chart-modal-backdrop" data-close-chart-modal></div>
    <section class="chart-modal-panel" role="dialog" aria-modal="true" aria-label="Загрузка графика">
      <header class="chart-modal-header">
        <div class="detail-title">Загрузка графика</div>
        <button
          type="button"
          class="chart-modal-icon-button chart-modal-icon-button--danger"
          data-close-chart-modal
          aria-label="Закрыть окно графика"
          title="Закрыть"
        >
          <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
            <path d="M5 5L15 15"></path>
            <path d="M15 5L5 15"></path>
          </svg>
        </button>
      </header>
      <div class="chart-modal-skeleton" role="status" aria-label="Загрузка графика">
        <div class="chart-modal-skeleton-summary">
          <span class="skeleton skeleton-line" style="width:38%"></span>
          <span class="skeleton skeleton-line" style="width:58%"></span>
          <span class="skeleton skeleton-line" style="width:30%"></span>
        </div>
        <div class="skeleton chart-modal-skeleton-chart"></div>
      </div>
    </section>
  `;

  modalRoot.querySelectorAll("[data-close-chart-modal]").forEach((element) => {
    element.addEventListener("click", closeChartModal);
  });
}

export async function openWashModal(key) {
  const requestId = ++modalRequestId;
  renderModalLoading();

  try {
    const detail = await getDetail(key);
    const navigation = getModalNavigation(key);
    prefetchChartPayload(detail.chart_data_url);
    prefetchWashContext(navigation.previous?.key || "");
    prefetchWashContext(navigation.next?.key || "");
    // Уточняем реальное состояние окна, чтобы сводка правильно показывалась/
    // скрывалась независимо от того, как окно развернули.
    await refreshWindowMaximizedState();
    if (requestId !== modalRequestId) {
      return;
    }

    modalRoot.hidden = false;
    syncOverlayState();
    modalRoot.innerHTML = `
      <div class="chart-modal-backdrop" data-close-chart-modal></div>
      <section class="chart-modal-panel" role="dialog" aria-modal="true" aria-label="Полноэкранный график мойки">
        <header class="chart-modal-header chart-modal-header--table">
          <div class="chart-modal-actions chart-modal-actions--top">
            <button type="button" class="chart-modal-button chart-modal-button--primary" data-download-pdf>Сохранить как PDF</button>
            <button type="button" class="chart-modal-button chart-modal-button--secondary" data-print-wash>Печать</button>
            <button
              type="button"
              class="chart-modal-icon-button chart-modal-icon-button--danger"
              data-close-chart-modal
              aria-label="Закрыть окно графика"
              title="Закрыть"
            >
              <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
                <path d="M5 5L15 15"></path>
                <path d="M15 5L5 15"></path>
              </svg>
            </button>
          </div>
          <div class="chart-modal-summary-card">
            <table class="chart-modal-table" aria-label="Параметры мойки">
              <tbody>
                <tr>
                  <th scope="row">Объект</th>
                  <td data-object-name-label>${escapeHtml(detail.object_name)}</td>
                </tr>
                <tr>
                  <th scope="row">Программа мойки</th>
                  <td>${escapeHtml(detail.program)}</td>
                </tr>
                <tr>
                  <th scope="row">Начало мойки</th>
                  <td>${escapeHtml(formatDateTime(detail.start_time || detail.date_time))}</td>
                </tr>
                <tr>
                  <th scope="row">Конец мойки</th>
                  <td>${escapeHtml(formatDateTime(detail.end_time))}</td>
                </tr>
                <tr>
                  <th scope="row">Длительность мойки</th>
                  <td>${escapeHtml(detail.duration)}</td>
                </tr>
                <tr data-wash-result-row>
                  <th scope="row">Результат</th>
                  <td><span class="${badgeClass(detail.status, detail.result_kind)}">${escapeHtml(detail.status)}</span></td>
                </tr>
                ${concentrationSummaryRows(detail)
                  .map(
                    ([label, value]) => `
                <tr>
                  <th scope="row">${escapeHtml(label)}</th>
                  <td>${escapeHtml(value)}</td>
                </tr>`
                  )
                  .join("")}
              </tbody>
            </table>
          </div>
        </header>
        <div class="chart-modal-frame">
          <div class="chart-modal-mini-title" aria-hidden="true">
            <span class="chart-modal-mini-object">${escapeHtml(detail.object_name)}</span>
            <span class="chart-modal-mini-wash">${escapeHtml(detail.program)}</span>
          </div>
          <div class="chart-host chart-host--modal" data-chart-host></div>
          <div class="technical-empty chart-empty" data-chart-empty hidden>
            Подготавливаю данные графика.
          </div>
        </div>
        <footer class="chart-modal-nav">
          <button
            type="button"
            class="chart-modal-nav-button chart-modal-nav-button--prev"
            data-open-wash-key="${navigation.previous ? escapeHtml(navigation.previous.key) : ""}"
            ${navigation.previous ? "" : "disabled"}
          >
            <span class="chart-modal-nav-icon" aria-hidden="true">←</span>
            <span class="chart-modal-nav-button-label">Предыдущая мойка</span>
          </button>
          <button
            type="button"
            class="chart-modal-nav-button chart-modal-nav-button--next"
            data-open-wash-key="${navigation.next ? escapeHtml(navigation.next.key) : ""}"
            ${navigation.next ? "" : "disabled"}
          >
            <span class="chart-modal-nav-icon" aria-hidden="true">→</span>
            <span class="chart-modal-nav-button-label">Следующая мойка</span>
          </button>
        </footer>
      </section>
    `;

    modalRoot.querySelectorAll("[data-close-chart-modal]").forEach((element) => {
      element.addEventListener("click", closeChartModal);
    });
    const pdfButton = modalRoot.querySelector("[data-download-pdf]");
    if (pdfButton) {
      pdfButton.addEventListener("click", () => {
        void saveGraphPdf(detail, pdfButton);
      });
    }
    const printButton = modalRoot.querySelector("[data-print-wash]");
    if (printButton) {
      printButton.addEventListener("click", () => {
        // runHandler, а не void: printReportDocument может отклониться (WebView
        // без печати) — иначе улетело бы в unhandled rejection.
        runHandler(printReportDocument(detail, "print"));
      });
    }
    modalRoot.querySelectorAll("[data-open-wash-key]").forEach((element) => {
      if (!element.dataset.openWashKey) {
        return;
      }
      element.addEventListener("click", () => runHandler(openWashModal(element.dataset.openWashKey)));
    });

    void mountChart(modalRoot, detail, requestId);
  } catch (_error) {
    if (requestId !== modalRequestId) {
      return;
    }

    modalRoot.hidden = false;
    syncOverlayState();
    modalRoot.innerHTML = `
      <div class="chart-modal-backdrop" data-close-chart-modal></div>
      <section class="chart-modal-panel" role="dialog" aria-modal="true" aria-label="Ошибка загрузки графика">
        <header class="chart-modal-header">
          <div class="detail-title">Не удалось открыть график</div>
          <button
            type="button"
            class="chart-modal-icon-button chart-modal-icon-button--danger"
            data-close-chart-modal
            aria-label="Закрыть окно графика"
            title="Закрыть"
          >
            <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
              <path d="M5 5L15 15"></path>
              <path d="M15 5L5 15"></path>
            </svg>
          </button>
        </header>
        <div class="technical-empty">Попробуй выбрать мойку ещё раз.</div>
      </section>
    `;

    modalRoot.querySelectorAll("[data-close-chart-modal]").forEach((element) => {
      element.addEventListener("click", closeChartModal);
    });
  }
}
