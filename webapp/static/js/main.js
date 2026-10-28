// Точка входа фронтенда (ES-модуль, подключается через <script type="module">).
// Собирает приложение из модулей: стартовый экран, затем (если открыт источник)
// рабочая область — корни окон, загрузка данных, клавиатура и обработчики.
// Тело обёрнуто в main(): ранний выход `if (!appState.hasWorkspace) return;`
// на верхнем уровне модуля запрещён. Модули всегда в strict mode.

import { apiJson } from "./core/api.js";
import { appState } from "./core/app-state.js";
import { initClock } from "./core/clock.js";
import {
  initDesktopTitlebar,
  initFolderDefaultButtons,
  initFolderPickerButtons,
} from "./core/desktop.js";
import { setOverlays, topOverlay } from "./core/overlays.js";
import { runHandler } from "./core/toast.js";
import { closeDbBrowser, initDbBrowser, openDbBrowser } from "./db-browser.js";
import {
  clearDetachedPrintDocument,
  clearPrintMode,
  exportWashesXlsx,
  initExport,
  saveWashRowPdf,
} from "./export.js";
import {
  fetchWorkspaceJobStatus,
  initWorkspaceJobStatusFeed,
  updateWorkspaceJobUi,
} from "./job-status.js";
import {
  closeObjectEditor,
  initObjectEditor,
  openObjectEditor,
  replaceProgramRows,
} from "./object-editor.js";
import { closeSettings, initSettings, openSettings } from "./settings/index.js";
import { initAutoUpdate, initWelcomeUpdateButton } from "./updates.js";
import { closeChartModal, initWashModal, mountChart, openWashModal } from "./wash-modal.js";
import { initFtpDiscovery } from "./welcome/ftp-discovery.js";
import { initHome } from "./welcome/home.js";
import { initPouScreen } from "./pou/screen.js";
import {
  initConnectedPanelWebView,
  initPanelGraphsContext,
  initWashWebViewButton,
} from "./welcome/panels.js";
import { invalidateWashCaches } from "./workspace/cache.js";
import { applyWashResultVisibility } from "./workspace/result-visibility.js";
import {
  DEFAULT_PERIOD_PRESET,
  replaceObjectRows,
  replaceWashRows,
  state,
} from "./workspace/store.js";
import {
  channelFilter,
  dayFilter,
  fillChannelFilter,
  fluidWashListQuery,
  invalidateRenderedWashWindow,
  isWashListVirtualized,
  openDayFilterButton,
  renderVirtualizedWashList,
  renderWashList,
  resetAllSearchFilters,
  cancelScheduledSearchRender,
  scheduleSearchRender,
  scheduleVirtualizedWashList,
  searchInput,
  sortOrder,
  syncDateFilterBounds,
  syncDayFilterButton,
  syncPeriodPresetButtons,
  syncSortButtons,
  washFilterCount,
  washList,
  washListMetrics,
} from "./workspace/wash-list.js";

function main() {

  // Экранирование — из wash-chart.js (классический скрипт, грузится раньше модулей).
  const { escapeHtml } = window.WashChart;
  const initialJobStatus =
    appState.jobStatus && typeof appState.jobStatus === "object"
      ? appState.jobStatus
      : { active: false, status: "idle" };

  initFolderPickerButtons();
  initFolderDefaultButtons();
  initFtpDiscovery();
  initPanelGraphsContext();
  initHome();
  initConnectedPanelWebView();
  initWashWebViewButton();
  initWelcomeUpdateButton();
  initDesktopTitlebar();
  initAutoUpdate();

  // Функции рабочей области объявлены ниже гейта (поднимаются), но вызываются
  // плашкой только по завершении задачи. hasWorkspace — геттер: флаг меняется
  // на лету в applyWorkspacePayload.
  const workspaceJobFeed = initWorkspaceJobStatusFeed(initialJobStatus, {
    hasWorkspace: () => appState.hasWorkspace,
    hydrateWorkspaceData,
    setScreenError,
    fetchWorkspaceData,
    applyWorkspacePayload,
  });

  // Экран трендов ПОУ — свой модуль; журнала CIP на нём нет.
  if (appState.pouVisible) {
    initClock();
    initPouScreen();
  }

  if (!appState.hasWorkspace) {
    return;
  }

  // Фоновое автообновление FTP: отдельный лёгкий поллер. Когда серверная фоновая
  // задача завершается, тихо подтягиваем свежие данные — без оверлея и перезагрузки.
  // Поллер нужен только FTP-источнику (у папки фоновых задач не бывает) и только
  // при видимой вкладке.
  (function initBackgroundRefreshWatcher() {
    const POLL_MS = 15000;
    let lastHandledJobId =
      initialJobStatus?.background && initialJobStatus?.status === "completed"
        ? initialJobStatus.id || ""
        : "";
    let timer = 0;
    let stopped = false;

    const stop = () => {
      if (timer) {
        window.clearTimeout(timer);
        timer = 0;
      }
    };

    const schedule = () => {
      if (stopped || timer || document.visibilityState === "hidden") {
        return;
      }
      timer = window.setTimeout(tick, POLL_MS);
    };

    async function tick() {
      timer = 0;
      try {
        const status = await fetchWorkspaceJobStatus();
        if (
          status &&
          status.background &&
          status.status === "completed" &&
          (status.id || "") !== lastHandledJobId
        ) {
          lastHandledJobId = status.id || "";
          try {
            await hydrateWorkspaceData({ resetScroll: false, keepUi: true });
          } catch (_error) {
            // Оставляем текущие данные, если результат обновления не удалось получить.
          }
        }
      } catch (_error) {
        // Игнорируем сбой опроса и повторяем на следующем тике.
      }
      schedule();
    }

    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") {
        stop();
      } else {
        schedule();
      }
    });

    // Для папки поллер не запускаем: фоновые обновления бывают только у FTP.
    if (appState.sourceKind === "folder") {
      stopped = true;
      stop();
    } else {
      schedule();
    }
  })();

  const clearDateFiltersButton = document.querySelector("#clearDateFilters");
  const exportXlsxButton = document.querySelector("#exportXlsx");
  const periodPresetButtons = Array.from(document.querySelectorAll("[data-period-preset]"));
  const openObjectEditorButton = document.querySelector("#openObjectEditor");
  const openDbBrowserButton = document.querySelector("#openDbBrowser");
  const screenPath = document.querySelector("#screenPath");
  const screenStats = document.querySelector("#screenStats");
  const screenErrorNotice = document.querySelector("#screenErrorNotice");
  const workspaceRefreshForm = document.querySelector("#workspaceRefreshForm");

  if (
    !washList ||
    !washFilterCount ||
    !searchInput ||
    !dayFilter ||
    !openDayFilterButton ||
    !clearDateFiltersButton ||
    !channelFilter ||
    !sortOrder
  ) {
    return;
  }

  const modalRoot = document.createElement("div");
  modalRoot.className = "chart-modal";
  modalRoot.hidden = true;
  document.body.append(modalRoot);
  initWashModal(modalRoot);

  const printRoot = document.createElement("div");
  printRoot.className = "chart-print-root";
  printRoot.hidden = true;
  document.body.append(printRoot);
  initExport(printRoot, {
    mountChart,
  });

  const objectEditorRoot = document.createElement("div");
  objectEditorRoot.className = "object-editor-modal";
  objectEditorRoot.hidden = true;
  document.body.append(objectEditorRoot);
  initObjectEditor(objectEditorRoot, {
    beforeOpen: () => {
      if (!modalRoot.hidden) {
        closeChartModal();
      }
    },
    hydrateWorkspaceData,
  });

  const settingsRoot = document.createElement("div");
  settingsRoot.className = "object-editor-modal";
  settingsRoot.hidden = true;
  document.body.append(settingsRoot);
  initSettings(settingsRoot, {
    beforeOpen: () => {
      if (!modalRoot.hidden) {
        closeChartModal();
      }
      if (!objectEditorRoot.hidden) {
        closeObjectEditor();
      }
    },
    hydrateWorkspaceData,
  });

  // Просмотр сырого содержимого `.db`: список таблиц и страницы строк.
  const dbBrowserRoot = document.createElement("div");
  dbBrowserRoot.className = "object-editor-modal";
  dbBrowserRoot.hidden = true;
  document.body.append(dbBrowserRoot);
  initDbBrowser(dbBrowserRoot, {
    beforeOpen: () => {
      if (!modalRoot.hidden) {
        closeChartModal();
      }
    },
  });

  // Модальные окна в порядке закрытия по Escape (верхнее — первым). Один список
  // для Escape, «/» и класса modal-open: раньше их перечисляли в трёх местах
  // вручную, и браузер БД выпал из одного из них.
  setOverlays([
    [dbBrowserRoot, closeDbBrowser],
    [settingsRoot, closeSettings],
    [objectEditorRoot, closeObjectEditor],
    [modalRoot, closeChartModal],
  ]);

  function renderSummaryPills(summary = {}) {
    const pills = [
      `<div class="stat-pill">Моек: ${Number(summary.cycle_count || 0)}</div>`,
      `<div class="stat-pill">Объектов: ${Number(summary.object_count || 0)}</div>`,
      `<div class="stat-pill">Баз: ${Number(summary.db_count || 0)}</div>`,
    ];

    if (Number(summary.archive_count || 0) > 0) {
      pills.push(`<div class="stat-pill">Архивов: ${Number(summary.archive_count || 0)}</div>`);
    }
    if (Number(summary.ftp_source_count || 0) > 0) {
      pills.push(`<div class="stat-pill">FTP: ${Number(summary.ftp_source_count || 0)}</div>`);
    }

    return pills.join("");
  }

  function setScreenError(message) {
    if (!screenErrorNotice) {
      return;
    }
    const text = String(message || "").trim();
    screenErrorNotice.textContent = text;
    screenErrorNotice.hidden = !text;
  }

  function applyWorkspaceMeta(payload = {}) {
    if (screenPath) {
      screenPath.textContent = String(payload.display_root || appState.displayRoot || "");
    }
    if (screenStats) {
      screenStats.innerHTML = renderSummaryPills(payload.summary || {});
    }
    setScreenError(payload.error || "");
  }

  function setWashListMessage(message) {
    washList.innerHTML = `<div class="technical-empty">${escapeHtml(message)}</div>`;
  }

  // Скелетон списка моек: серые строки-заготовки в тех же колонках, что и реальные.
  function setWashListSkeleton(count = 8) {
    const row = `
      <div class="wash-row wash-row--skeleton" aria-hidden="true">
        <div class="wash-cell wash-cell--primary"><span class="skeleton skeleton-line" style="width:72%"></span></div>
        <div class="wash-cell"><span class="skeleton skeleton-line" style="width:82%"></span></div>
        <div class="wash-cell"><span class="skeleton skeleton-line" style="width:74%"></span></div>
        <div class="wash-cell"><span class="skeleton skeleton-line" style="width:48%"></span></div>
        <div class="wash-cell wash-cell--status"><span class="skeleton skeleton-badge"></span></div>
      </div>`;
    washList.innerHTML = `<div class="wash-list-skeleton" role="status" aria-label="Загрузка списка моек">${row.repeat(count)}</div>`;
  }

  function clearWorkspaceDataCaches({ keepUi = false } = {}) {
    invalidateWashCaches();
    // Фоновое обновление не должно закрывать открытые окна пользователя —
    // кэши инвалидируются, а UI остаётся как есть.
    if (keepUi) {
      return;
    }
    if (!modalRoot.hidden) {
      closeChartModal();
    }
    if (!objectEditorRoot.hidden) {
      closeObjectEditor();
    }
  }

  function fetchWorkspaceData() {
    return apiJson("/api/workspace-data");
  }

  function applyWorkspacePayload(payload, { resetScroll = false } = {}) {
    appState.hasWorkspace = Boolean(payload?.has_analysis);
    appState.displayRoot = String(payload?.display_root || "");
    appState.summary = payload?.summary || {};
    appState.error = String(payload?.error || "");
    appState.jobStatus = payload?.job_status || appState.jobStatus || {};
    state.serverTzOffsetMin =
      typeof payload?.tz_offset_min === "number" && Number.isFinite(payload.tz_offset_min)
        ? payload.tz_offset_min
        : null;

    applyWorkspaceMeta(payload);
    replaceObjectRows(payload?.object_rows);
    replaceProgramRows(payload?.program_rows);
    replaceWashRows(payload?.wash_rows);
    fillChannelFilter();
    syncDateFilterBounds();
    syncDayFilterButton();
    syncPeriodPresetButtons();
    renderWashList({ resetScroll });

    if (openObjectEditorButton) {
      openObjectEditorButton.disabled = false;
    }
    if (openDbBrowserButton) {
      openDbBrowserButton.disabled = false;
    }
  }

  let workspaceHydrationToken = 0;

  async function hydrateWorkspaceData({ resetScroll = false, keepUi = false } = {}) {
    // Скелетон — только при первой загрузке (пустой список); при фоновом
    // обновлении оставляем текущие мойки, чтобы не мигало.
    if (!state.washRows.length) {
      setWashListSkeleton();
    }
    const token = ++workspaceHydrationToken;
    const payload = await fetchWorkspaceData();
    // Пока ждали ответ, стартовала более свежая гидрация — не перетираем её
    // данные устаревшим ответом.
    if (token !== workspaceHydrationToken) {
      return payload;
    }
    clearWorkspaceDataCaches({ keepUi });
    applyWorkspacePayload(payload, { resetScroll });
    return payload;
  }

  function startWorkspaceRefresh() {
    return apiJson("/api/workspace/refresh", {
      method: "POST",
      errorMessage: "Не удалось запустить обновление.",
    });
  }

  // ---- Клавиатурная навигация ------------------------------------------------
  function isTypingTarget(element) {
    if (!element) {
      return false;
    }
    const tag = element.tagName;
    return (
      tag === "INPUT" ||
      tag === "TEXTAREA" ||
      tag === "SELECT" ||
      element.isContentEditable === true
    );
  }

  function isAnyOverlayOpen() {
    return !printRoot.hidden || topOverlay() !== null;
  }

  function focusWashRowByKey(key) {
    if (!key) {
      return false;
    }
    const escaped = CSS.escape(key);
    const element = washList.querySelector(`.wash-row[data-key="${escaped}"]`);
    if (!element) {
      return false;
    }
    element.focus({ preventScroll: true });
    element.scrollIntoView({ block: "nearest" });
    return true;
  }

  // Стрелочная навигация по списку с учётом виртуализации: если целевая строка
  // вне отрисованного окна, сначала подвигаем scrollTop и синхронно
  // перерисовываем окно, затем ставим фокус.
  function moveWashSelection(delta, { fromEdge = false } = {}) {
    const items = state.displayItems;
    if (!items || !items.length) {
      return false;
    }
    const rowIndices = state.displayRowIndices;
    if (!rowIndices || !rowIndices.length) {
      return false;
    }

    const active = document.activeElement;
    const currentKey =
      active && washList.contains(active)
        ? active.closest("[data-key]")?.dataset.key || ""
        : "";
    let pos = currentKey
      ? rowIndices.findIndex((index) => items[index].row.key === currentKey)
      : -1;

    let nextPos;
    if (pos === -1 || fromEdge) {
      nextPos = delta > 0 ? 0 : rowIndices.length - 1;
    } else {
      nextPos = Math.min(rowIndices.length - 1, Math.max(0, pos + delta));
    }

    const targetIndex = rowIndices[nextPos];
    const targetKey = items[targetIndex].row.key;

    if (isWashListVirtualized()) {
      const offsets = state.displayOffsets;
      const rowHeight = washListMetrics.rowHeight;
      const viewport = washList.clientHeight;
      const top = offsets[targetIndex] || 0;
      if (top < washList.scrollTop) {
        washList.scrollTop = top;
      } else if (top + rowHeight > washList.scrollTop + viewport) {
        washList.scrollTop = top + rowHeight - viewport;
      }
      renderVirtualizedWashList();
    }

    return focusWashRowByKey(targetKey);
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      topOverlay()?.[1]();
      return;
    }

    // «/» — быстрый фокус в поиск (когда не печатаем в поле и нет открытых окон).
    if (
      event.key === "/" &&
      !event.ctrlKey &&
      !event.metaKey &&
      !event.altKey &&
      !isTypingTarget(event.target) &&
      !isAnyOverlayOpen() &&
      searchInput
    ) {
      event.preventDefault();
      searchInput.focus();
      searchInput.select();
    }
  });
  window.addEventListener("afterprint", () => {
    clearPrintMode();
    clearDetachedPrintDocument();
  });
  washList.addEventListener("scroll", scheduleVirtualizedWashList);
  washList.addEventListener("click", (event) => {
    const pdfButton = event.target.closest("[data-download-row-pdf]");
    if (pdfButton && washList.contains(pdfButton)) {
      const row = pdfButton.closest("[data-key]");
      if (!row?.dataset.key) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      void saveWashRowPdf(row.dataset.key, pdfButton);
      return;
    }

    const row = event.target.closest("[data-key]");
    if (!row || !washList.contains(row) || !row.dataset.key) {
      return;
    }
    runHandler(openWashModal(row.dataset.key));
  });
  washList.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      moveWashSelection(event.key === "ArrowDown" ? 1 : -1);
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      moveWashSelection(event.key === "Home" ? 1 : -1, { fromEdge: true });
      return;
    }
    if (event.key !== "Enter" && event.key !== " ") {
      return;
    }
    if (event.target.closest("[data-download-row-pdf]")) {
      return;
    }
    const row = event.target.closest("[data-key]");
    if (!row || !washList.contains(row) || !row.dataset.key) {
      return;
    }
    event.preventDefault();
    runHandler(openWashModal(row.dataset.key));
  });
  // Смена ширины окна может изменить высоту строки (медиа-запросы) и режим
  // виртуализации — кэш отрисованного окна сбрасываем, чтобы высоты перемерились.
  // Дебаунс: во время перетаскивания рамки resize сыплется десятками в секунду,
  // а нам достаточно перемерить строки один раз, когда размер устоялся.

  let resizeDebounceTimer = 0;
  window.addEventListener("resize", () => {
    window.clearTimeout(resizeDebounceTimer);
    resizeDebounceTimer = window.setTimeout(() => {
      invalidateRenderedWashWindow();
      scheduleVirtualizedWashList();
    }, 120);
  });

  fluidWashListQuery.addEventListener("change", () => {
    invalidateRenderedWashWindow();
    scheduleVirtualizedWashList();
  });

  document.querySelectorAll("[data-sort-value]").forEach((button) => {
    button.addEventListener("click", () => {
      const nextValue = String(button.dataset.sortValue || "");
      if (!nextValue || sortOrder.value === nextValue) {
        return;
      }
      sortOrder.value = nextValue;
      syncSortButtons();
      renderWashList({ resetScroll: true });
    });
  });

  // channelFilter и sortOrder — скрытые input'ы (значение меняют кнопки тулбара,
  // которые сами вызывают renderWashList): input/change они не эмитят, слушатели
  // на них никогда не срабатывали.
  // ↓ из поиска переводит фокус на первую строку списка.
  searchInput.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      moveWashSelection(1, { fromEdge: true });
    }
  });
  searchInput.addEventListener("input", scheduleSearchRender);
  searchInput.addEventListener("change", () => {
    // Отменяем отложенный дебаунс-рендер, чтобы не рисовать список дважды.
    cancelScheduledSearchRender();
    renderWashList({ resetScroll: true });
  });

  function handleDayFilterChange() {
    state.activePeriodPreset = "";
    syncPeriodPresetButtons();
    syncDateFilterBounds();
    syncDayFilterButton();
    renderWashList({ resetScroll: true });
  }

  // Только change: input у <input type="date"> эмитится вместе с change при
  // выборе даты в календаре — список фильтровался и рисовался дважды.
  dayFilter.addEventListener("change", handleDayFilterChange);

  clearDateFiltersButton.addEventListener("click", () => {
    resetAllSearchFilters();
    renderWashList({ resetScroll: true });
  });

  if (exportXlsxButton) {
    exportXlsxButton.addEventListener("click", () => {
      void exportWashesXlsx(exportXlsxButton);
    });
  }

  if (workspaceRefreshForm) {
    workspaceRefreshForm.addEventListener("submit", async (event) => {
      event.preventDefault();

      const submitButton = workspaceRefreshForm.querySelector('button[type="submit"]');
      const originalLabel = submitButton?.textContent || "";
      if (submitButton) {
        submitButton.disabled = true;
        submitButton.textContent = "Обновляю...";
      }

      setScreenError("");

      try {
        const payload = await startWorkspaceRefresh();
        // Подключаем мониторинг только после подтверждённого старта новой задачи:
        // иначе первое сообщение потока — терминальный статус ПРЕДЫДУЩЕЙ задачи,
        // поток закрывается, и новая задача остаётся без наблюдения.
        workspaceJobFeed?.ensureMonitoring?.();
        if (payload?.job) {
          updateWorkspaceJobUi(payload.job, { keepVisible: true });
        }
      } catch (error) {
        setScreenError(error instanceof Error ? error.message : "Не удалось запустить обновление.");
      } finally {
        if (submitButton) {
          submitButton.disabled = false;
          submitButton.textContent = originalLabel;
        }
      }
    });
  }

  if (openObjectEditorButton) {
    openObjectEditorButton.disabled = true;
    openObjectEditorButton.addEventListener("click", () => openObjectEditor());
  }

  if (openDbBrowserButton) {
    openDbBrowserButton.disabled = true;
    openDbBrowserButton.addEventListener("click", () => openDbBrowser());
  }

  const openSettingsButton = document.querySelector("#openSettings");
  if (openSettingsButton) {
    openSettingsButton.addEventListener("click", () => runHandler(openSettings()));
  }

  initClock();
  // Автопроверки при старте нет: обновления проверяются только по кнопке в
  // настройках — одна проверка на нажатие, без походов к GitHub за спиной.
  applyWashResultVisibility();

  periodPresetButtons.forEach((button) => {
    button.addEventListener("click", () => {
      state.activePeriodPreset = button.dataset.periodPreset || DEFAULT_PERIOD_PRESET;
      dayFilter.value = "";
      syncDateFilterBounds();
      syncDayFilterButton();
      syncPeriodPresetButtons();
      renderWashList({ resetScroll: true });
    });
  });

  syncSortButtons();
  applyWorkspaceMeta({
    display_root: appState.displayRoot,
    summary: appState.summary,
    error: appState.error,
  });
  syncDayFilterButton();
  syncPeriodPresetButtons();
  setWashListSkeleton();
  void hydrateWorkspaceData({ resetScroll: true }).catch(() => {
    setScreenError("Не удалось загрузить список моек.");
    setWashListMessage("Не удалось загрузить список моек.");
    if (openObjectEditorButton) {
      openObjectEditorButton.disabled = false;
    }
    if (openDbBrowserButton) {
      openDbBrowserButton.disabled = false;
    }
  });
}

main();
