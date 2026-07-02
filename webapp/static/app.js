(function () {
  const appState = window.__WASH_APP__ || {};
  const folderPickerButtons = Array.from(document.querySelectorAll("[data-folder-picker]"));
  const folderDefaultButtons = Array.from(document.querySelectorAll("[data-folder-default]"));
  // Подписи результата мойки (ключи/значения по умолчанию совпадают с сервером).
  const RESULT_LABEL_FIELDS = [
    { key: "completed", label: "Завершено штатно", def: "Завершено штатно" },
    { key: "check", label: "Требует проверки", def: "Требует проверки" },
  ];
  // Должно совпадать с LINE_STYLE_OPTIONS в wash-chart.js и CHART_LINE_STYLE_IDS на сервере.
  const CHART_LINE_STYLE_OPTIONS = [
    { id: "solid", label: "Сплошная" },
    { id: "dashed", label: "Штриховая" },
    { id: "dashdot", label: "Штрих-пунктир" },
    { id: "dotted", label: "Точечная" },
    { id: "longdash", label: "Длинный штрих" },
  ];
  const initialJobStatus =
    appState.jobStatus && typeof appState.jobStatus === "object"
      ? appState.jobStatus
      : { active: false, status: "idle" };
  const sourceTabs = Array.from(document.querySelectorAll("[data-source-tab]"));
  const sourcePanels = Array.from(document.querySelectorAll("[data-source-panel]"));

  function hasDesktopFolderPickerApi() {
    return typeof window.pywebview?.api?.choose_folder === "function";
  }

  function setActiveWelcomeSource(source) {
    if (!sourceTabs.length || !sourcePanels.length) {
      return;
    }

    sourceTabs.forEach((button) => {
      const isActive = button.dataset.sourceTab === source;
      button.classList.toggle("is-active", isActive);
      button.classList.toggle("ghost", !isActive);
      button.setAttribute("aria-pressed", isActive ? "true" : "false");
    });

    sourcePanels.forEach((panel) => {
      panel.hidden = panel.dataset.sourcePanel !== source;
    });
  }

  function initWelcomeSourceTabs() {
    if (!sourceTabs.length || !sourcePanels.length) {
      return;
    }

    const activeTab = sourceTabs.find((button) => button.classList.contains("is-active")) || sourceTabs[0];
    setActiveWelcomeSource(activeTab?.dataset.sourceTab || "ftp");

    sourceTabs.forEach((button) => {
      button.addEventListener("click", () => {
        setActiveWelcomeSource(button.dataset.sourceTab || "ftp");
      });
    });
  }

  function submitForm(form) {
    if (!form) {
      return;
    }

    if (typeof form.requestSubmit === "function") {
      form.requestSubmit();
      return;
    }

    form.submit();
  }

  async function requestFolderPath(initialPath = "") {
    if (!hasDesktopFolderPickerApi()) {
      throw new Error("desktop-folder-picker-unavailable");
    }

    const response = await window.pywebview.api.choose_folder({
      initial_path: initialPath,
    });

    if (response?.cancelled) {
      return null;
    }

    if (!response?.ok || !response?.path) {
      throw new Error("desktop-folder-picker-failed");
    }

    return response.path;
  }

  function initFolderPickerButtons() {
    if (!folderPickerButtons.length) {
      return;
    }

    folderPickerButtons.forEach((button) => {
      button.addEventListener("click", async () => {
        const targetInputId = button.dataset.targetInput;
        const submitFormId = button.dataset.submitForm;
        const targetInput = targetInputId ? document.getElementById(targetInputId) : null;
        const targetForm = submitFormId ? document.getElementById(submitFormId) : targetInput?.form || null;
        const originalLabel = button.textContent?.trim() || "Выбрать папку";

        button.disabled = true;
        button.textContent = "Выбираю папку...";

        try {
          const selectedPath = await requestFolderPath(targetInput?.value || "");
          if (!selectedPath) {
            return;
          }

          if (targetInput) {
            targetInput.value = selectedPath;
          }

          if (targetForm) {
            submitForm(targetForm);
          }
        } catch (_error) {
          window.alert("Не удалось открыть выбор папки. Попробуйте ещё раз.");
        } finally {
          button.disabled = false;
          button.textContent = originalLabel;
        }
      });
    });
  }

  function initFolderDefaultButtons() {
    if (!folderDefaultButtons.length) {
      return;
    }

    folderDefaultButtons.forEach((button) => {
      button.addEventListener("click", () => {
        const targetInputId = button.dataset.targetInput;
        const targetInput = targetInputId ? document.getElementById(targetInputId) : null;
        const defaultPath = button.dataset.defaultPath || "";
        if (!targetInput || !defaultPath) {
          return;
        }
        targetInput.value = defaultPath;
        targetInput.focus();
      });
    });
  }

  // Кастомная шапка окна (frameless desktop-режим). В обычном браузере
  // window.pywebview отсутствует — шапка остаётся скрытой, лейаут не меняется.
  function initDesktopTitlebar() {
    const titlebar = document.querySelector("[data-app-titlebar]");
    if (!titlebar) {
      return;
    }

    const callWindowApi = (method) => {
      const api = window.pywebview && window.pywebview.api;
      if (api && typeof api[method] === "function") {
        Promise.resolve(api[method]()).catch(() => {});
      }
    };

    // Признак развёрнутого окна на body — по нему в уменьшённом режиме модалка
    // графика показывает только сам график (без сводки и параметров кривых).
    const setMaximizedState = (maximized) => {
      document.body.classList.toggle("window-maximized", Boolean(maximized));
    };

    const toggleMaximize = () => {
      const api = window.pywebview && window.pywebview.api;
      if (api && typeof api.toggle_maximize === "function") {
        Promise.resolve(api.toggle_maximize())
          .then((result) => {
            if (result && typeof result.maximized === "boolean") {
              setMaximizedState(result.maximized);
            }
          })
          .catch(() => {});
      }
    };

    titlebar.querySelector("[data-window-min]")?.addEventListener("click", () => callWindowApi("minimize_window"));
    titlebar.querySelector("[data-window-max]")?.addEventListener("click", toggleMaximize);
    titlebar.querySelector("[data-window-close]")?.addEventListener("click", () => callWindowApi("close_window"));
    titlebar.querySelector("[data-titlebar-drag]")?.addEventListener("dblclick", toggleMaximize);

    const enableDesktopShell = () => {
      document.body.classList.add("desktop-shell");
      titlebar.hidden = false;
      // Приложение открывается в уменьшённом окне (не развёрнуто).
      setMaximizedState(false);
    };

    if (window.pywebview && window.pywebview.api) {
      enableDesktopShell();
    } else {
      // pywebview внедряет мост чуть позже загрузки страницы.
      window.addEventListener("pywebviewready", enableDesktopShell, { once: true });
    }
  }

  initWelcomeSourceTabs();
  initFolderPickerButtons();
  initFolderDefaultButtons();
  initDesktopTitlebar();

  const workspaceJobRoot = document.querySelector("[data-workspace-job]");
  const workspaceJobMessage = workspaceJobRoot?.querySelector("[data-workspace-job-message]");
  const workspaceJobTarget = workspaceJobRoot?.querySelector("[data-workspace-job-target]");
  const workspaceJobPhase = workspaceJobRoot?.querySelector("[data-workspace-job-phase]");
  const workspaceJobCount = workspaceJobRoot?.querySelector("[data-workspace-job-count]");
  const workspaceJobBar = workspaceJobRoot?.querySelector("[data-workspace-job-bar]");
  const workspaceJobItem = workspaceJobRoot?.querySelector("[data-workspace-job-item]");
  const workspaceJobCancelButton = workspaceJobRoot?.querySelector("[data-workspace-job-cancel]");

  function formatWorkspaceJobCount(status) {
    const current = Number(status.current || 0);
    const total = Number(status.total || 0);
    if (total > 0) {
      return `${Math.min(current, total)} из ${total}`;
    }
    if (current > 0) {
      return String(current);
    }
    return "Подсчитываю";
  }

  function workspaceJobProgressWidth(status) {
    const total = Number(status.total || 0);
    const current = Number(status.current || 0);
    if (total <= 0) {
      return 12;
    }
    return Math.max(12, Math.min(100, (current / total) * 100));
  }

  async function fetchWorkspaceJobStatus() {
    const response = await fetch("/api/workspace-job");
    if (!response.ok) {
      throw new Error("workspace-job-status-failed");
    }
    return response.json();
  }

  function isTerminalWorkspaceJobStatus(status) {
    return ["completed", "failed", "cancelled"].includes(String(status?.status || ""));
  }

  function setWorkspaceJobVisible(isVisible) {
    if (!workspaceJobRoot) {
      return;
    }
    workspaceJobRoot.hidden = !isVisible;
  }

  function updateWorkspaceJobUi(status, options = {}) {
    if (!workspaceJobRoot) {
      return;
    }

    const { keepVisible = false } = options;
    // Фоновое автообновление FTP не показывает блокирующий оверлей.
    const isBackground = Boolean(status?.background);
    setWorkspaceJobVisible(!isBackground && (keepVisible || Boolean(status?.active)));

    if (workspaceJobMessage) {
      workspaceJobMessage.textContent = status.message || "Обрабатываю источник";
    }
    if (workspaceJobTarget) {
      workspaceJobTarget.textContent = status.display_target || status.target_root || "";
    }
    if (workspaceJobPhase) {
      workspaceJobPhase.textContent = status.phase || "queued";
    }
    if (workspaceJobCount) {
      workspaceJobCount.textContent = formatWorkspaceJobCount(status);
    }
    if (workspaceJobBar) {
      workspaceJobBar.style.width = `${workspaceJobProgressWidth(status)}%`;
      const progressRoot = workspaceJobBar.parentElement;
      if (progressRoot) {
        progressRoot.setAttribute("aria-valuemax", String(Number(status.total || 0)));
        progressRoot.setAttribute("aria-valuenow", String(Number(status.current || 0)));
      }
    }
    if (workspaceJobItem) {
      workspaceJobItem.textContent = status.item || "";
      workspaceJobItem.hidden = !status.item;
    }
    if (workspaceJobCancelButton) {
      const isCancelling = status.status === "cancelling";
      const isActive = Boolean(status.active);
      workspaceJobCancelButton.disabled = !isActive || isCancelling;
      workspaceJobCancelButton.textContent = isCancelling ? "Отменяю..." : "Отменить";
    }
  }

  async function handleTerminalWorkspaceJob(status) {
    // Фоновое автообновление обрабатывается отдельным поллером (без оверлея и
    // перезагрузки), поэтому здесь его игнорируем.
    if (status.background) {
      return;
    }

    updateWorkspaceJobUi(status, { keepVisible: true });

    if (!appState.hasWorkspace) {
      window.location.reload();
      return;
    }

    if (status.status === "completed") {
      try {
        await hydrateWorkspaceData({ resetScroll: false });
        setScreenError("");
        setWorkspaceJobVisible(false);
      } catch (_error) {
        window.location.reload();
      }
      return;
    }

    try {
      const payload = await fetchWorkspaceData();
      if (payload?.has_analysis) {
        applyWorkspacePayload(payload, { resetScroll: false });
      }
    } catch (_error) {
      // Keep the current screen data as-is when the refresh result cannot be fetched.
    }

    setScreenError(status.error || status.message || "");
    setWorkspaceJobVisible(false);
  }

  function initWorkspaceJobStatusFeed() {
    if (!workspaceJobRoot) {
      return {
        ensureMonitoring() {},
      };
    }

    updateWorkspaceJobUi(initialJobStatus);

    if (workspaceJobCancelButton) {
      workspaceJobCancelButton.addEventListener("click", async () => {
        if (workspaceJobCancelButton.disabled) {
          return;
        }

        workspaceJobCancelButton.disabled = true;
        workspaceJobCancelButton.textContent = "Отменяю...";

        try {
          await fetch("/api/workspace-job/cancel", { method: "POST" });
        } catch (_error) {
          workspaceJobCancelButton.disabled = false;
          workspaceJobCancelButton.textContent = "Отменить";
        }
      });
    }

    let eventSource = null;
    let pollTimer = 0;
    let fallbackStarted = false;

    const scheduleNextPoll = () => {
      pollTimer = window.setTimeout(tick, 1000);
    };

    const closeStream = () => {
      eventSource?.close();
      eventSource = null;
    };

    const handleStatus = async (status) => {
      if (!status.active && isTerminalWorkspaceJobStatus(status)) {
        closeStream();
        await handleTerminalWorkspaceJob(status);
        return;
      }

      updateWorkspaceJobUi(status);
      if (status.active && !eventSource && !pollTimer) {
        scheduleNextPoll();
      }
    };

    const tick = async () => {
      pollTimer = 0;
      try {
        const status = await fetchWorkspaceJobStatus();
        await handleStatus(status);
        if (!status.active || isTerminalWorkspaceJobStatus(status)) {
          return;
        }
      } catch (_error) {
        // Leave the current overlay state intact and retry on the next tick.
      }

      scheduleNextPoll();
    };

    const startPollingFallback = () => {
      if (fallbackStarted) {
        return;
      }
      fallbackStarted = true;
      scheduleNextPoll();
    };

    const startStream = () => {
      if (eventSource || typeof window.EventSource !== "function") {
        return Boolean(eventSource);
      }

      eventSource = new window.EventSource("/api/workspace-job/stream");
      eventSource.onmessage = async (event) => {
        try {
          const status = JSON.parse(event.data);
          await handleStatus(status);
        } catch (_error) {
          closeStream();
          startPollingFallback();
        }
      };
      eventSource.onerror = () => {
        closeStream();
        startPollingFallback();
      };
      return true;
    };

    // Подписываемся на поток только если задача реально выполняется при загрузке
    // страницы. Иначе уже завершённый (failed/cancelled) статус сразу считался бы
    // «терминальным» и вызывал бы window.location.reload() — бесконечная
    // перезагрузка после неудачного подключения к FTP.
    if (initialJobStatus.active) {
      startStream() || startPollingFallback();
    }

    window.addEventListener("beforeunload", () => {
      closeStream();
      if (pollTimer) {
        window.clearTimeout(pollTimer);
      }
    });

    return {
      ensureMonitoring() {
        if (!startStream()) {
          startPollingFallback();
        }
      },
    };
  }

  const workspaceJobFeed = initWorkspaceJobStatusFeed();

  if (!appState.hasWorkspace) {
    return;
  }

  // Фоновое автообновление FTP: отдельный лёгкий поллер. Когда серверная фоновая
  // задача завершается, тихо подтягиваем свежие данные — без оверлея и перезагрузки.
  (function initBackgroundRefreshWatcher() {
    const POLL_MS = 15000;
    let lastHandledJobId =
      initialJobStatus?.background && initialJobStatus?.status === "completed"
        ? initialJobStatus.id || ""
        : "";
    let timer = 0;

    const schedule = () => {
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
            await hydrateWorkspaceData({ resetScroll: false });
          } catch (_error) {
            // Оставляем текущие данные, если результат обновления не удалось получить.
          }
        }
      } catch (_error) {
        // Игнорируем сбой опроса и повторяем на следующем тике.
      }
      schedule();
    }

    window.addEventListener("beforeunload", () => {
      if (timer) {
        window.clearTimeout(timer);
      }
    });
    schedule();
  })();

  const WASH_LIST_ROW_HEIGHT = 69;
  const WASH_LIST_OVERSCAN = 8;
  const SEARCH_INPUT_DEBOUNCE_MS = 180;
  const DEFAULT_PERIOD_PRESET = "7d";
  const state = {
    washRows: [],
    washRowIndexesByObjectKey: new Map(),
    objectRows: [],
    filteredRows: [],
    dateBounds: null,
    activePeriodPreset: DEFAULT_PERIOD_PRESET,
  };
  const detailCache = new Map();
  const detailRequestCache = new Map();
  const chartPayloadCache = new Map();
  const chartPayloadRequestCache = new Map();
  const DETAIL_CACHE_LIMIT = 200;
  const CHART_PAYLOAD_CACHE_LIMIT = 80;

  function setBoundedCacheEntry(cache, key, value, limit) {
    // Простая LRU-эвикция: не даём кэшам расти неограниченно между обновлениями данных.
    if (cache.has(key)) {
      cache.delete(key);
    }
    cache.set(key, value);
    while (cache.size > limit) {
      const oldestKey = cache.keys().next().value;
      cache.delete(oldestKey);
    }
  }
  let modalRequestId = 0;
  let activeModalKey = "";
  let restoreDocumentTitle = null;
  let activeModalChartReady = Promise.resolve(false);
  let washListRenderFrame = 0;
  let searchRenderTimer = 0;

  const washList = document.querySelector("#washList");
  const washFilterCount = document.querySelector("#washFilterCount");
  const searchInput = document.querySelector("#searchInput");
  const dayFilter = document.querySelector("#dayFilter");
  const openDayFilterButton = document.querySelector("#openDayFilter");
  const clearDateFiltersButton = document.querySelector("#clearDateFilters");
  const periodPresetButtons = Array.from(document.querySelectorAll("[data-period-preset]"));
  const channelFilter = document.querySelector("#channelFilter");
  const sortOrder = document.querySelector("#sortOrder");
  const openObjectEditorButton = document.querySelector("#openObjectEditor");
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

  const printRoot = document.createElement("div");
  printRoot.className = "chart-print-root";
  printRoot.hidden = true;
  document.body.append(printRoot);

  const objectEditorRoot = document.createElement("div");
  objectEditorRoot.className = "object-editor-modal";
  objectEditorRoot.hidden = true;
  document.body.append(objectEditorRoot);

  const settingsRoot = document.createElement("div");
  settingsRoot.className = "object-editor-modal";
  settingsRoot.hidden = true;
  document.body.append(settingsRoot);

  const WASH_RESULT_PREF_KEY = "opticipShowWashResultV1";

  function isWashResultVisible() {
    try {
      return window.localStorage.getItem(WASH_RESULT_PREF_KEY) !== "0";
    } catch (error) {
      return true;
    }
  }

  function applyWashResultVisibility() {
    document.body.classList.toggle("wash-result-hidden", !isWashResultVisible());
  }

  function setWashResultVisible(visible) {
    try {
      window.localStorage.setItem(WASH_RESULT_PREF_KEY, visible ? "1" : "0");
    } catch (error) {
      /* localStorage может быть недоступен — просто применяем к текущей сессии */
    }
    applyWashResultVisibility();
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function badgeClass(status) {
    return status.startsWith("Завершено") ? "badge ok" : "badge warn";
  }

  function formatModalDateTime(value) {
    const match = String(value ?? "").match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/);
    if (!match) {
      return String(value ?? "—");
    }

    const [, year, month, day, hours, minutes, seconds] = match;
    return `${day}.${month}.${year}. ${hours}.${minutes}.${seconds}`;
  }

  function formatListDateTime(value) {
    const match = String(value ?? "").match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/);
    if (!match) {
      return escapeHtml(value ?? "—");
    }

    const [, year, month, day, hours, minutes, seconds] = match;
    return `${day}.${month}.${year} ${hours}:${minutes}:${seconds}`;
  }

  function buildSearchBlob(row) {
    return [
      row.object,
      row.program,
      row.date_time,
      row.source_name,
      row.status,
      `Канал ${row.channel}`,
    ]
      .join(" ")
      .toLowerCase();
  }

  function buildObjectEditorSearchBlob(row) {
    return [
      `Канал ${row.channel}`,
      `Объект ${row.object_id}`,
      row.object_name,
      row.base_object_name,
    ]
      .join(" ")
      .toLowerCase();
  }

  function normalizeObjectName(value) {
    return String(value ?? "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function sortObjectRows(rows) {
    return rows.sort(
      (left, right) =>
        Number(left.channel) - Number(right.channel) ||
        Number(left.object_id) - Number(right.object_id)
    );
  }

  function replaceObjectRows(rows) {
    state.objectRows = sortObjectRows(
      (Array.isArray(rows) ? rows : []).map((row) => {
        const nextRow = { ...row };
        nextRow.search_blob = buildObjectEditorSearchBlob(nextRow);
        return nextRow;
      })
    );
  }

  function buildWashObjectKey(channel, objectId) {
    return `${Number(channel) || 0}:${Number(objectId) || 0}`;
  }

  function rebuildWashRowIndexes() {
    const nextIndexes = new Map();
    state.washRows.forEach((row, index) => {
      const key = buildWashObjectKey(row.channel, row.object_id);
      if (!nextIndexes.has(key)) {
        nextIndexes.set(key, []);
      }
      nextIndexes.get(key).push(index);
    });
    state.washRowIndexesByObjectKey = nextIndexes;
  }

  function replaceWashRows(rows) {
    state.washRows = (Array.isArray(rows) ? rows : []).map((row) => {
      const nextRow = { ...row };
      if (!nextRow.search_blob) {
        nextRow.search_blob = buildSearchBlob(nextRow);
      }
      return nextRow;
    });
    state.dateBounds = null;
    rebuildWashRowIndexes();
  }

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

  function clearWorkspaceDataCaches() {
    detailCache.clear();
    detailRequestCache.clear();
    chartPayloadCache.clear();
    chartPayloadRequestCache.clear();
    if (!modalRoot.hidden) {
      closeChartModal();
    }
    if (!objectEditorRoot.hidden) {
      closeObjectEditor();
    }
  }

  async function fetchWorkspaceData() {
    const response = await fetch("/api/workspace-data");
    if (!response.ok) {
      throw new Error("workspace-data-request-failed");
    }
    return response.json();
  }

  function applyWorkspacePayload(payload, { resetScroll = false } = {}) {
    appState.hasWorkspace = Boolean(payload?.has_analysis);
    appState.hasAnalysis = Boolean(payload?.has_analysis);
    appState.displayRoot = String(payload?.display_root || "");
    appState.summary = payload?.summary || {};
    appState.error = String(payload?.error || "");
    appState.jobStatus = payload?.job_status || appState.jobStatus || {};

    applyWorkspaceMeta(payload);
    replaceObjectRows(payload?.object_rows);
    replaceWashRows(payload?.wash_rows);
    fillChannelFilter();
    syncDateFilterBounds();
    syncDayFilterButton();
    syncPeriodPresetButtons();
    renderWashList({ resetScroll });

    if (openObjectEditorButton) {
      openObjectEditorButton.disabled = false;
    }
  }

  async function hydrateWorkspaceData({ resetScroll = false } = {}) {
    setWashListMessage("Загружаю список моек...");
    const payload = await fetchWorkspaceData();
    clearWorkspaceDataCaches();
    applyWorkspacePayload(payload, { resetScroll });
    return payload;
  }

  async function startWorkspaceRefresh() {
    const response = await fetch("/api/workspace/refresh", { method: "POST" });
    if (!response.ok) {
      let errorMessage = "Не удалось запустить обновление.";
      try {
        const payload = await response.json();
        if (payload?.detail) {
          errorMessage = String(payload.detail);
        }
      } catch (_error) {
        // Fall back to the generic message.
      }
      throw new Error(errorMessage);
    }
    return response.json();
  }

  function renderObjectEditorChannelChoices(selectedValue = 1) {
    const selectedChannel = Number(selectedValue || 1);
    return Array.from({ length: 5 }, (_, index) => index + 1)
      .map(
        (channel) => `
          <button
            type="button"
            class="object-editor-choice${channel === selectedChannel ? " is-selected" : ""}"
            data-choice-group="channel"
            data-choice-value="${channel}"
            aria-pressed="${channel === selectedChannel ? "true" : "false"}"
          >
            ${channel}
          </button>
        `
      )
      .join("");
  }

  function renderObjectEditorIdChoices(selectedValue = 1) {
    const selectedId = Number(selectedValue || 1);
    return Array.from({ length: 30 }, (_, index) => index + 1)
      .map(
        (objectId) => `
          <button
            type="button"
            class="object-editor-choice object-editor-choice--id${objectId === selectedId ? " is-selected" : ""}"
            data-choice-group="object_id"
            data-choice-value="${objectId}"
            aria-pressed="${objectId === selectedId ? "true" : "false"}"
          >
            ${objectId}
          </button>
        `
      )
      .join("");
  }

  function syncObjectEditorChoiceGroup(root, groupName, rawValue) {
    if (!root) {
      return;
    }

    const normalizedValue = String(rawValue);
    root.querySelectorAll(`[data-choice-group="${groupName}"]`).forEach((button) => {
      const isSelected = String(button.dataset.choiceValue || "") === normalizedValue;
      button.classList.toggle("is-selected", isSelected);
      button.setAttribute("aria-pressed", isSelected ? "true" : "false");
    });
  }

  function syncSortButtons() {
    const currentValue = String(sortOrder.value || "date_desc");
    document.querySelectorAll("[data-sort-value]").forEach((button) => {
      const isActive = String(button.dataset.sortValue || "") === currentValue;
      button.classList.toggle("is-active", isActive);
      button.setAttribute("aria-pressed", isActive ? "true" : "false");
    });
  }

  function syncChannelButtons() {
    const currentValue = String(channelFilter.value || "");
    document.querySelectorAll("[data-channel-value]").forEach((button) => {
      const isActive = String(button.dataset.channelValue || "") === currentValue;
      button.classList.toggle("is-active", isActive);
      button.setAttribute("aria-pressed", isActive ? "true" : "false");
    });
  }

  function getLocalDateKey(value = new Date()) {
    const date = value instanceof Date ? value : new Date(value);
    const year = String(date.getFullYear());
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  function getPeriodPresetStartTs() {
    if (state.activePeriodPreset === "all" || !state.activePeriodPreset) {
      return null;
    }
    if (state.activePeriodPreset === "today") {
      return new Date(new Date().setHours(0, 0, 0, 0)).getTime() / 1000;
    }
    if (state.activePeriodPreset === "7d") {
      return (Date.now() - 7 * 24 * 60 * 60 * 1000) / 1000;
    }
    if (state.activePeriodPreset === "30d") {
      return (Date.now() - 30 * 24 * 60 * 60 * 1000) / 1000;
    }
    return null;
  }

  function syncPeriodPresetButtons() {
    periodPresetButtons.forEach((button) => {
      button.classList.toggle("is-active", button.dataset.periodPreset === state.activePeriodPreset);
    });
  }

  function getAvailableDateBounds() {
    if (state.dateBounds) {
      return state.dateBounds;
    }

    const dates = state.washRows.map((row) => row.start_day).filter(Boolean).sort();
    state.dateBounds = {
      min: dates[0] || "",
      max: dates[dates.length - 1] || "",
    };
    return state.dateBounds;
  }

  function syncDateFilterBounds() {
    const { min, max } = getAvailableDateBounds();
    dayFilter.min = min;
    dayFilter.max = max;
  }

  function syncDayFilterButton() {
    const hasSelectedDay = Boolean(dayFilter.value);
    openDayFilterButton.classList.toggle("is-active", hasSelectedDay);
    const label = hasSelectedDay ? `Выбрана дата: ${dayFilter.value}` : "Открыть календарь";
    openDayFilterButton.title = label;
    openDayFilterButton.setAttribute("aria-label", label);
  }

  function resetAllSearchFilters() {
    if (searchRenderTimer) {
      window.clearTimeout(searchRenderTimer);
      searchRenderTimer = 0;
    }

    searchInput.value = "";
    channelFilter.value = "";
    dayFilter.value = "";
    state.activePeriodPreset = DEFAULT_PERIOD_PRESET;
    syncChannelButtons();
    syncDateFilterBounds();
    syncDayFilterButton();
    syncPeriodPresetButtons();
  }

  function getModalNavigation(key) {
    const rows = state.filteredRows.length ? state.filteredRows : getFilteredRows();
    const currentIndex = rows.findIndex((row) => row.key === key);
    if (currentIndex < 0) {
      return { previous: null, next: null };
    }

    return {
      previous: rows[currentIndex - 1] ?? null,
      next: rows[currentIndex + 1] ?? null,
    };
  }

  function fillChannelFilter() {
    const channelOptionsRoot = document.querySelector("#channelOptions");
    if (!channelOptionsRoot) {
      return;
    }

    const channels = [...new Set(state.washRows.map((item) => Number(item.channel)))]
      .filter((channel) => Number.isInteger(channel) && channel >= 1 && channel <= 5)
      .sort((a, b) => a - b);

    if (channelFilter.value && !channels.includes(Number(channelFilter.value))) {
      channelFilter.value = "";
    }

    const buttons = [
      '<button type="button" class="toolbar-channel-option" data-channel-value="">Все</button>',
      ...channels.map(
        (channel) =>
          `<button type="button" class="toolbar-channel-option" data-channel-value="${channel}">Канал ${channel}</button>`
      ),
    ];
    channelOptionsRoot.innerHTML = buttons.join("");

    channelOptionsRoot.querySelectorAll("[data-channel-value]").forEach((button) => {
      button.addEventListener("click", () => {
        const nextValue = String(button.dataset.channelValue || "");
        if (channelFilter.value === nextValue) {
          return;
        }
        channelFilter.value = nextValue;
        syncChannelButtons();
        renderWashList({ resetScroll: true });
      });
    });

    syncChannelButtons();
  }

  function sortRows(rows) {
    const mode = sortOrder.value;
    const sorted = [...rows];

    sorted.sort((left, right) => {
      const leftStartTs = Number(left.start_ts || 0);
      const rightStartTs = Number(right.start_ts || 0);

      if (mode === "date_asc") {
        return leftStartTs - rightStartTs;
      }

      if (mode === "object_asc") {
        return left.object.localeCompare(right.object, "ru") || rightStartTs - leftStartTs;
      }

      if (mode === "duration_desc") {
        return Number(right.duration_seconds || 0) - Number(left.duration_seconds || 0) || rightStartTs - leftStartTs;
      }

      return rightStartTs - leftStartTs;
    });

    return sorted;
  }

  function getFilteredRows() {
    const query = searchInput.value.trim().toLowerCase();
    const selectedDay = dayFilter.value;
    const channel = channelFilter.value;
    const presetStartTs = getPeriodPresetStartTs();
    const todayKey = getLocalDateKey();

    const filtered = state.washRows.filter((row) => {
      if (selectedDay && row.start_day !== selectedDay) {
        return false;
      }

      if (!selectedDay && state.activePeriodPreset === "today" && row.start_day !== todayKey) {
        return false;
      }

      if (!selectedDay && presetStartTs !== null && state.activePeriodPreset !== "today") {
        if (Number(row.start_ts || 0) < presetStartTs) {
          return false;
        }
      }

      if (channel && String(row.channel) !== channel) {
        return false;
      }

      if (!query) {
        return true;
      }

      return String(row.search_blob || "").includes(query);
    });

    return sortRows(filtered);
  }

  function renderWashRow(row) {
    return `
      <div class="wash-row" data-key="${escapeHtml(row.key)}" role="button" tabindex="0">
        <div class="wash-cell wash-cell--primary">
          <div class="wash-entry-time" title="${escapeHtml(formatListDateTime(row.date_time))}">${formatListDateTime(row.date_time)}</div>
        </div>
        <div class="wash-cell">
          <div class="wash-entry-object" title="${escapeHtml(row.object)}">${escapeHtml(row.object)}</div>
        </div>
        <div class="wash-cell">
          <div class="wash-entry-program" title="${escapeHtml(row.program)}">${escapeHtml(row.program)}</div>
        </div>
        <div class="wash-cell">
          <span class="wash-chip" title="${escapeHtml(row.duration)}">${escapeHtml(row.duration)}</span>
        </div>
        <div class="wash-cell wash-cell--status">
          <span class="${badgeClass(row.status)}" title="${escapeHtml(row.status)}">${escapeHtml(row.status)}</span>
          <button type="button" class="wash-row-pdf-button" data-download-row-pdf title="PDF" aria-label="PDF">PDF</button>
        </div>
      </div>
    `;
  }

  function renderVirtualizedWashList({ resetScroll = false } = {}) {
    if (resetScroll) {
      washList.scrollTop = 0;
    }

    if (!state.filteredRows.length) {
      washList.innerHTML = '<div class="technical-empty">Мойки не найдены</div>';
      return;
    }

    const viewportHeight = Math.max(washList.clientHeight, WASH_LIST_ROW_HEIGHT);
    const scrollTop = washList.scrollTop;
    const visibleCount = Math.ceil(viewportHeight / WASH_LIST_ROW_HEIGHT) + WASH_LIST_OVERSCAN * 2;
    const startIndex = Math.max(0, Math.floor(scrollTop / WASH_LIST_ROW_HEIGHT) - WASH_LIST_OVERSCAN);
    const endIndex = Math.min(state.filteredRows.length, startIndex + visibleCount);
    const topSpacerHeight = startIndex * WASH_LIST_ROW_HEIGHT;
    const bottomSpacerHeight = Math.max(0, (state.filteredRows.length - endIndex) * WASH_LIST_ROW_HEIGHT);

    washList.innerHTML = [
      topSpacerHeight
        ? `<div class="wash-list-spacer" style="height:${topSpacerHeight}px" aria-hidden="true"></div>`
        : "",
      state.filteredRows.slice(startIndex, endIndex).map(renderWashRow).join(""),
      bottomSpacerHeight
        ? `<div class="wash-list-spacer" style="height:${bottomSpacerHeight}px" aria-hidden="true"></div>`
        : "",
    ].join("");
  }

  function scheduleVirtualizedWashList() {
    if (washListRenderFrame) {
      return;
    }
    washListRenderFrame = window.requestAnimationFrame(() => {
      washListRenderFrame = 0;
      renderVirtualizedWashList();
    });
  }

  function updateWashFilterCount(count) {
    if (!washFilterCount) {
      return;
    }
    washFilterCount.textContent = `Найдено моек: ${count}`;
  }

  function renderWashList(options = {}) {
    const { resetScroll = true } = options;
    state.filteredRows = getFilteredRows();
    updateWashFilterCount(state.filteredRows.length);
    renderVirtualizedWashList({ resetScroll });
  }

  async function getDetail(key) {
    if (detailCache.has(key)) {
      return detailCache.get(key);
    }

    if (detailRequestCache.has(key)) {
      return detailRequestCache.get(key);
    }

    const request = fetch(`/api/wash-details?key=${encodeURIComponent(key)}`)
      .then((response) => {
        if (!response.ok) {
          throw new Error("wash-details-request-failed");
        }
        return response.json();
      })
      .then((payload) => {
        setBoundedCacheEntry(detailCache, key, payload, DETAIL_CACHE_LIMIT);
        return payload;
      })
      .finally(() => {
        detailRequestCache.delete(key);
      });

    detailRequestCache.set(key, request);
    return request;
  }

  async function getChartPayload(url) {
    if (!url) {
      throw new Error("wash-chart-url-missing");
    }

    if (chartPayloadCache.has(url)) {
      return chartPayloadCache.get(url);
    }

    if (chartPayloadRequestCache.has(url)) {
      return chartPayloadRequestCache.get(url);
    }

    const request = fetch(url)
      .then((response) => {
        if (!response.ok) {
          throw new Error("chart-data-request-failed");
        }
        return response.json();
      })
      .then((payload) => {
        setBoundedCacheEntry(chartPayloadCache, url, payload, CHART_PAYLOAD_CACHE_LIMIT);
        return payload;
      })
      .finally(() => {
        chartPayloadRequestCache.delete(url);
      });

    chartPayloadRequestCache.set(url, request);
    return request;
  }

  function prefetchChartPayload(url) {
    if (!url) {
      return;
    }
    void getChartPayload(url).catch(() => {});
  }

  function prefetchWashContext(key) {
    if (!key) {
      return;
    }

    void getDetail(key)
      .then((detail) => {
        if (detail?.chart_data_url) {
          return getChartPayload(detail.chart_data_url);
        }
        return null;
      })
      .catch(() => {});
  }

  function scheduleSearchRender() {
    if (searchRenderTimer) {
      window.clearTimeout(searchRenderTimer);
    }

    searchRenderTimer = window.setTimeout(() => {
      searchRenderTimer = 0;
      renderWashList({ resetScroll: true });
    }, SEARCH_INPUT_DEBOUNCE_MS);
  }

  function findObjectEditorRow(channel, objectId) {
    return (
      state.objectRows.find(
        (row) =>
          Number(row.channel) === Number(channel) && Number(row.object_id) === Number(objectId)
      ) || null
    );
  }

  function findDuplicateObjectName(channel, objectId, objectName) {
    const normalizedName = normalizeObjectName(objectName).toLowerCase();
    if (!normalizedName) {
      return null;
    }

    return (
      state.objectRows.find(
        (row) =>
          !(
            Number(row.channel) === Number(channel) && Number(row.object_id) === Number(objectId)
          ) && normalizeObjectName(row.object_name).toLowerCase() === normalizedName
      ) || null
    );
  }

  function findFirstAvailableObjectSlot() {
    for (let channel = 1; channel <= 5; channel += 1) {
      for (let objectId = 1; objectId <= 30; objectId += 1) {
        if (!findObjectEditorRow(channel, objectId)) {
          return { channel, objectId };
        }
      }
    }

    return { channel: 1, objectId: 1 };
  }

  async function persistObjectName(channel, objectId, name = "", mode = "set") {
    const response = await fetch("/api/object-name", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        channel,
        object_id: objectId,
        name,
        mode,
      }),
    });

    if (!response.ok) {
      let errorMessage = "Не удалось сохранить новое название объекта.";
      try {
        const payload = await response.json();
        if (payload?.detail) {
          errorMessage = String(payload.detail);
        }
      } catch (_error) {
        // Fall back to the generic message.
      }
      throw new Error(errorMessage);
    }

    return response.json();
  }

  function applyObjectNameToWashData(channel, objectId, objectName) {
    const rowIndexes = state.washRowIndexesByObjectKey.get(buildWashObjectKey(channel, objectId)) || [];
    rowIndexes.forEach((index) => {
      const row = state.washRows[index];
      if (!row) {
        return;
      }

      const updatedRow = {
        ...row,
        object: objectName,
      };
      updatedRow.search_blob = buildSearchBlob(updatedRow);
      state.washRows[index] = updatedRow;
    });

    detailCache.forEach((detail, key) => {
      if (Number(detail.channel) !== Number(channel) || Number(detail.object_id) !== Number(objectId)) {
        return;
      }
      detailCache.set(key, {
        ...detail,
        object_name: objectName,
      });
    });

    renderWashList({ resetScroll: false });
  }

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

  function renderPrintSummaryRows(detail) {
    return [
      ["Объект", detail.object_name],
      ["Программа мойки", detail.program],
      ["Начало мойки", formatModalDateTime(detail.start_time || detail.date_time)],
      ["Конец мойки", formatModalDateTime(detail.end_time)],
      ["Длительность мойки", detail.duration],
      ["Результат", detail.status],
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

  function clearPrintMode() {
    document.body.classList.remove("modal-print-mode");
    if (restoreDocumentTitle) {
      document.title = restoreDocumentTitle;
      restoreDocumentTitle = null;
    }
  }

  function clearDetachedPrintDocument() {
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

  async function preparePrintDocument(detail) {
    try {
      await activeModalChartReady;
    } catch (_error) {
      // The visible empty state will explain failed chart loading.
    }

    if (!modalRoot.hidden && detail?.key === activeModalKey) {
      activeModalChartReady = mountChart(modalRoot, detail, modalRequestId);
      try {
        await activeModalChartReady;
      } catch (_error) {
        // Keep the print/export path available even when the chart cannot be refreshed.
      }
    }

    await waitForNextFrame();
    await waitForNextFrame();
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

  function syncOverlayState() {
    const hasVisibleOverlay =
      !modalRoot.hidden || !objectEditorRoot.hidden || !settingsRoot.hidden;
    document.body.classList.toggle("modal-open", hasVisibleOverlay);
  }

  function hasDesktopPdfApi() {
    return typeof window.pywebview?.api?.save_graph_pdf === "function";
  }

  async function saveGraphPdf(detail, button) {
    let cleanupDetachedDocument = true;
    const originalLabel = button?.textContent || "Сохранить как PDF";

    if (button) {
      button.disabled = true;
      button.textContent = "Сохраняю PDF...";
    }

    try {
      if (!hasDesktopPdfApi()) {
        cleanupDetachedDocument = false;
        await printReportDocument(detail, "pdf");
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
        await printReportDocument(detail, "pdf");
        return;
      }

      if (!response?.ok && !response?.cancelled) {
        throw new Error("desktop-pdf-save-failed");
      }
    } catch (_error) {
      window.alert("Не удалось сохранить PDF. Попробуйте ещё раз.");
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

  async function printReportDocument(detail, intent = "print") {
    await prepareDetachedPrintDocument(detail);

    const originalTitle = document.title;
    if (intent === "pdf" && detail) {
      document.title = `${buildPrintFileName(detail)}.pdf`;
    }
    restoreDocumentTitle = originalTitle;

    document.body.classList.add("modal-print-mode");
    await waitForNextFrame();
    await waitForNextFrame();
    window.print();
    window.setTimeout(() => {
      clearPrintMode();
      clearDetachedPrintDocument();
    }, 1000);
  }

  async function printWashDetail(detail, intent = "print") {
    await printReportDocument(detail, intent);
  }

  async function saveWashRowPdf(key, button) {
    try {
      const detail = await getDetail(key);
      await saveGraphPdf(detail, button);
    } catch (_error) {
      window.alert("Не удалось сохранить PDF. Попробуйте ещё раз.");
    }
  }

  async function mountChart(root, detail, requestId = modalRequestId) {
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

  function closeObjectEditor() {
    objectEditorRoot.hidden = true;
    objectEditorRoot.innerHTML = "";
    syncOverlayState();
  }

  function closeSettings() {
    settingsRoot.hidden = true;
    settingsRoot.innerHTML = "";
    syncOverlayState();
  }

  async function fetchAppSettings() {
    const response = await fetch("/api/settings", { headers: { Accept: "application/json" } });
    if (!response.ok) {
      throw new Error("settings-fetch-failed");
    }
    const payload = await response.json();
    return payload && typeof payload.settings === "object" ? payload.settings : {};
  }

  async function saveAppSettings(patch) {
    const response = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: patch }),
    });
    if (!response.ok) {
      throw new Error("settings-save-failed");
    }
    const payload = await response.json();
    return payload && typeof payload.settings === "object" ? payload.settings : patch;
  }

  async function fetchChartStyles() {
    const response = await fetch("/api/chart-styles", { headers: { Accept: "application/json" } });
    if (!response.ok) {
      throw new Error("chart-styles-fetch-failed");
    }
    const payload = await response.json();
    return {
      series: payload && typeof payload.series === "object" ? payload.series : {},
      defaults: Array.isArray(payload?.defaults) ? payload.defaults : [],
    };
  }

  async function saveChartStyles(series) {
    const response = await fetch("/api/chart-styles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ series }),
    });
    if (!response.ok) {
      throw new Error("chart-styles-save-failed");
    }
    const payload = await response.json();
    return payload && typeof payload.series === "object" ? payload.series : series;
  }

  function renderChartStyleControls(defaults, series) {
    if (!defaults.length) {
      return '<div class="settings-chart-empty">Нет настраиваемых кривых.</div>';
    }
    return defaults
      .map((def) => {
        const saved = series[def.id] || {};
        const color = isValidHexColorLike(saved.color) ? saved.color : def.color;
        const lineStyle = CHART_LINE_STYLE_OPTIONS.some((option) => option.id === saved.lineStyle)
          ? saved.lineStyle
          : def.lineStyle;
        const options = CHART_LINE_STYLE_OPTIONS.map(
          (option) =>
            `<option value="${option.id}" ${option.id === lineStyle ? "selected" : ""}>${escapeHtml(option.label)}</option>`
        ).join("");
        return `
          <div class="settings-chart-row" style="--settings-chart-color: ${escapeHtml(color)};">
            <span class="settings-chart-swatch" aria-hidden="true"></span>
            <span class="settings-chart-name">${escapeHtml(def.label)}</span>
            <span class="wash-chart-color-input-shell">
              <input type="color" value="${escapeHtml(color)}" data-chart-style-color="${escapeHtml(def.id)}" aria-label="Цвет: ${escapeHtml(def.label)}">
            </span>
            <span class="wash-chart-select-shell">
              <select data-chart-style-line="${escapeHtml(def.id)}" aria-label="Тип линии: ${escapeHtml(def.label)}">${options}</select>
            </span>
          </div>
        `;
      })
      .join("");
  }

  function isValidHexColorLike(value) {
    return /^#[0-9a-f]{6}$/i.test(String(value || ""));
  }

  async function openSettings() {
    if (!modalRoot.hidden) {
      closeChartModal();
    }
    if (!objectEditorRoot.hidden) {
      closeObjectEditor();
    }
    settingsRoot.hidden = false;
    syncOverlayState();

    let settings = { ftp_auto_refresh_enabled: true, ftp_auto_refresh_minutes: 5, default_folder_path: "" };
    try {
      settings = { ...settings, ...(await fetchAppSettings()) };
    } catch (_error) {
      // Не удалось получить настройки — показываем значения по умолчанию.
    }

    let chartStyles = { series: {}, defaults: [] };
    try {
      chartStyles = await fetchChartStyles();
    } catch (_error) {
      // Стили графика недоступны — секцию покажем пустой.
    }

    // Пользователь мог закрыть окно, пока грузились настройки.
    if (settingsRoot.hidden) {
      return;
    }

    const resultLabels = settings.result_labels || {};
    settingsRoot.innerHTML = `
      <div class="object-editor-backdrop" data-close-settings></div>
      <section class="object-editor-panel object-editor-panel--settings" role="dialog" aria-modal="true" aria-label="Настройки">
        <header class="settings-header">
          <div class="settings-header-title">
            <span class="settings-header-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" focusable="false"><path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 0 0 .12-.61l-1.92-3.32a.49.49 0 0 0-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.48.48 0 0 0-.48-.41h-3.84a.48.48 0 0 0-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96a.49.49 0 0 0-.59.22L2.74 8.87a.49.49 0 0 0 .12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58a.49.49 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6a3.6 3.6 0 1 1 0-7.2 3.6 3.6 0 0 1 0 7.2z"></path></svg>
            </span>
            <h2>Настройки</h2>
          </div>
          <button type="button" class="chart-modal-icon-button chart-modal-icon-button--danger" data-close-settings aria-label="Закрыть настройки" title="Закрыть">
            <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
              <path d="M5 5L15 15"></path>
              <path d="M15 5L5 15"></path>
            </svg>
          </button>
        </header>
        <div class="settings-body">
          <details class="settings-section">
            <summary class="settings-section-title">Отображение</summary>
            <div class="settings-section-body">
              <label class="settings-option">
                <span class="settings-option-text"><strong>Результат мойки</strong></span>
                <input type="checkbox" data-setting-wash-result ${isWashResultVisible() ? "checked" : ""}>
              </label>
            </div>
          </details>
          <details class="settings-section">
            <summary class="settings-section-title">Подписи результата</summary>
            <div class="settings-section-body">
              ${RESULT_LABEL_FIELDS.map((field) => `
                <div class="settings-option settings-option--stacked">
                  <span class="settings-option-text"><strong>${escapeHtml(field.label)}</strong></span>
                  <input type="text" class="settings-text-input" data-setting-result-label="${field.key}" maxlength="120" placeholder="${escapeHtml(field.def)}" value="${escapeHtml(resultLabels[field.key] || field.def)}" autocomplete="off" spellcheck="false">
                </div>
              `).join("")}
            </div>
          </details>
          <details class="settings-section">
            <summary class="settings-section-title">Источник данных</summary>
            <div class="settings-section-body">
              <div class="settings-option settings-option--stacked">
                <span class="settings-option-text"><strong>Папка по умолчанию</strong></span>
                <input type="text" class="settings-text-input" data-setting-default-folder placeholder="Встроенная папка datalog" value="${escapeHtml(settings.default_folder_path || "")}" autocomplete="off" spellcheck="false">
              </div>
            </div>
          </details>
          <details class="settings-section">
            <summary class="settings-section-title">Автообновление FTP</summary>
            <div class="settings-section-body">
              <label class="settings-option">
                <span class="settings-option-text"><strong>Включено</strong></span>
                <input type="checkbox" data-setting-ftp-auto-refresh ${settings.ftp_auto_refresh_enabled ? "checked" : ""}>
              </label>
              <label class="settings-option">
                <span class="settings-option-text"><strong>Интервал, мин</strong></span>
                <input type="number" min="1" max="1440" step="1" data-setting-ftp-auto-refresh-minutes value="${Number(settings.ftp_auto_refresh_minutes) || 5}">
              </label>
            </div>
          </details>
          <details class="settings-section">
            <summary class="settings-section-title">График</summary>
            <div class="settings-section-body">
              <div class="settings-option settings-option--stacked">
                <span class="settings-option-text"><strong>Цвета и линии</strong></span>
                <div class="settings-chart-grid" data-chart-style-grid>${renderChartStyleControls(chartStyles.defaults, chartStyles.series)}</div>
                <div class="settings-chart-actions">
                  <button type="button" class="ghost" data-chart-style-reset>Сбросить</button>
                </div>
              </div>
            </div>
          </details>
        </div>
      </section>
    `;
    settingsRoot.querySelectorAll("[data-close-settings]").forEach((element) => {
      element.addEventListener("click", closeSettings);
    });
    const washResultToggle = settingsRoot.querySelector("[data-setting-wash-result]");
    if (washResultToggle) {
      washResultToggle.addEventListener("change", (event) => {
        setWashResultVisible(event.currentTarget.checked);
      });
    }

    const autoRefreshToggle = settingsRoot.querySelector("[data-setting-ftp-auto-refresh]");
    const autoRefreshMinutes = settingsRoot.querySelector("[data-setting-ftp-auto-refresh-minutes]");

    const persistAutoRefresh = async () => {
      const minutesRaw = Number(autoRefreshMinutes?.value);
      const minutes = Number.isFinite(minutesRaw) ? Math.min(1440, Math.max(1, Math.round(minutesRaw))) : 5;
      try {
        const saved = await saveAppSettings({
          ftp_auto_refresh_enabled: Boolean(autoRefreshToggle?.checked),
          ftp_auto_refresh_minutes: minutes,
        });
        // Нормализованное сервером значение возвращаем в поле.
        if (autoRefreshMinutes && saved && Number.isFinite(Number(saved.ftp_auto_refresh_minutes))) {
          autoRefreshMinutes.value = Number(saved.ftp_auto_refresh_minutes);
        }
      } catch (_error) {
        setScreenError("Не удалось сохранить настройки.");
      }
    };

    if (autoRefreshToggle) {
      autoRefreshToggle.addEventListener("change", persistAutoRefresh);
    }
    if (autoRefreshMinutes) {
      autoRefreshMinutes.addEventListener("change", persistAutoRefresh);
    }

    const defaultFolderInput = settingsRoot.querySelector("[data-setting-default-folder]");
    if (defaultFolderInput) {
      defaultFolderInput.addEventListener("change", async (event) => {
        try {
          const saved = await saveAppSettings({ default_folder_path: String(event.currentTarget.value || "").trim() });
          if (saved && typeof saved.default_folder_path === "string") {
            event.currentTarget.value = saved.default_folder_path;
          }
        } catch (_error) {
          setScreenError("Не удалось сохранить настройки.");
        }
      });
    }

    const chartStyleGrid = settingsRoot.querySelector("[data-chart-style-grid]");
    const chartStyleReset = settingsRoot.querySelector("[data-chart-style-reset]");

    const collectChartStyleSeries = () => {
      const series = {};
      if (!chartStyleGrid) {
        return series;
      }
      chartStyleGrid.querySelectorAll("[data-chart-style-color]").forEach((input) => {
        const id = input.dataset.chartStyleColor;
        const lineSelect = chartStyleGrid.querySelector(`[data-chart-style-line="${id}"]`);
        series[id] = {
          color: input.value,
          lineStyle: lineSelect ? lineSelect.value : "solid",
        };
      });
      return series;
    };

    const persistChartStyles = async () => {
      try {
        const saved = await saveChartStyles(collectChartStyleSeries());
        // Применяем к кэшу графиков, чтобы следующий открытый график сразу учёл изменения.
        window.WashChart?.setSeriesStyles?.(saved);
      } catch (_error) {
        setScreenError("Не удалось сохранить оформление графика.");
      }
    };

    if (chartStyleGrid) {
      chartStyleGrid.addEventListener("change", (event) => {
        const target = event.target;
        if (!target?.dataset) {
          return;
        }
        if ("chartStyleColor" in target.dataset) {
          const row = target.closest(".settings-chart-row");
          if (row) {
            row.style.setProperty("--settings-chart-color", target.value);
          }
        }
        if ("chartStyleColor" in target.dataset || "chartStyleLine" in target.dataset) {
          void persistChartStyles();
        }
      });
    }

    if (chartStyleReset) {
      chartStyleReset.addEventListener("click", async () => {
        try {
          await saveChartStyles({});
          const fresh = await fetchChartStyles();
          if (chartStyleGrid) {
            chartStyleGrid.innerHTML = renderChartStyleControls(fresh.defaults, fresh.series);
          }
          window.WashChart?.setSeriesStyles?.(fresh.series);
        } catch (_error) {
          setScreenError("Не удалось сбросить оформление графика.");
        }
      });
    }

    const resultLabelInputs = settingsRoot.querySelectorAll("[data-setting-result-label]");
    const persistResultLabels = async () => {
      const labels = {};
      resultLabelInputs.forEach((input) => {
        labels[input.dataset.settingResultLabel] = String(input.value || "").trim();
      });
      try {
        await saveAppSettings({ result_labels: labels });
        // Обновляем список моек, чтобы новые подписи применились сразу.
        if (appState.hasWorkspace) {
          hydrateWorkspaceData({ resetScroll: false }).catch(() => {});
        }
      } catch (_error) {
        setScreenError("Не удалось сохранить настройки.");
      }
    };
    resultLabelInputs.forEach((input) => {
      input.addEventListener("change", persistResultLabels);
    });
  }

  function renderObjectEditorRows() {
    if (!state.objectRows.length) {
      return '<div class="technical-empty">Объекты не найдены.</div>';
    }

    return state.objectRows
      .map(
        (row) => `
          <form class="object-editor-row" data-object-editor-form>
            <input type="hidden" name="channel" value="${escapeHtml(row.channel)}">
            <input type="hidden" name="object_id" value="${escapeHtml(row.object_id)}">
            <div class="object-editor-row-meta">
              <div class="object-editor-row-identity">
                <span class="object-editor-token">Канал ${escapeHtml(row.channel)}</span>
                <span class="object-editor-token">Объект ${escapeHtml(row.object_id)}</span>
              </div>
            </div>
            <div class="object-editor-row-controls">
              <input
                class="object-editor-name-input"
                type="text"
                name="object_name"
                value="${escapeHtml(row.object_name)}"
                placeholder="Название объекта"
                autocomplete="off"
                spellcheck="false"
              >
            </div>
            <div class="object-editor-row-actions">
              <button type="submit" class="object-editor-row-button object-editor-row-button--primary">Сохранить</button>
              <button
                type="button"
                class="ghost object-editor-row-button"
                data-object-editor-reset
                data-channel="${escapeHtml(row.channel)}"
                data-object-id="${escapeHtml(row.object_id)}"
                data-object-name="${escapeHtml(row.object_name)}"
              >
                Сбросить
              </button>
            </div>
          </form>
        `
      )
      .join("");
  }

  function closeAddObjectDialog() {
    const dialog = objectEditorRoot.querySelector("[data-object-editor-create]");
    if (dialog) {
      dialog.hidden = true;
    }
  }

  function openAddObjectDialog() {
    const dialog = objectEditorRoot.querySelector("[data-object-editor-create]");
    if (!dialog) {
      return;
    }
    dialog.hidden = false;
    const { channel: defaultChannel, objectId: defaultObjectId } = findFirstAvailableObjectSlot();
    const channelField = dialog.querySelector('input[name="channel"]');
    const objectIdField = dialog.querySelector('input[name="object_id"]');
    const nameField = dialog.querySelector('input[name="object_name"]');
    if (channelField) {
      channelField.value = String(defaultChannel);
      syncObjectEditorChoiceGroup(dialog, "channel", defaultChannel);
    }
    if (objectIdField) {
      objectIdField.value = String(defaultObjectId);
      syncObjectEditorChoiceGroup(dialog, "object_id", defaultObjectId);
    }
    if (nameField) {
      nameField.value = "";
      window.setTimeout(() => nameField.focus(), 0);
    }
    updateAddObjectDialogState(dialog);
  }

  function getAddObjectDialogState(dialog) {
    if (!dialog) {
      return {
        blocked: true,
        severity: "error",
        message: "Не удалось открыть форму добавления объекта.",
      };
    }

    const channel = Number(dialog.querySelector('input[name="channel"]')?.value || 0);
    const objectId = Number(dialog.querySelector('input[name="object_id"]')?.value || 0);
    const objectName = normalizeObjectName(dialog.querySelector('input[name="object_name"]')?.value || "");

    if (channel < 1 || channel > 5) {
      return {
        blocked: true,
        severity: "error",
        message: "Выбери канал от 1 до 5.",
      };
    }

    if (!Number.isInteger(objectId) || objectId < 1 || objectId > 30) {
      return {
        blocked: true,
        severity: "error",
        message: "Выбери object id в диапазоне от 1 до 30.",
      };
    }

    const existingRow = findObjectEditorRow(channel, objectId);
    if (existingRow) {
      return {
        blocked: true,
        severity: "error",
        message: `Для канала ${channel} и object id ${objectId} уже есть запись «${existingRow.object_name}».`,
      };
    }

    if (!objectName) {
      return {
        blocked: true,
        severity: "ok",
        message: "Слот свободен. Введи название объекта, чтобы добавить запись.",
      };
    }

    if (objectName.length > 120) {
      return {
        blocked: true,
        severity: "error",
        message: "Название объекта не должно быть длиннее 120 символов.",
      };
    }

    const duplicateNameRow = findDuplicateObjectName(channel, objectId, objectName);
    if (duplicateNameRow) {
      return {
        blocked: false,
        severity: "warning",
        message:
          `Такое название уже используется у канала ${duplicateNameRow.channel}, object id ${duplicateNameRow.object_id}. ` +
          "Проверь, что дублирование действительно нужно.",
      };
    }

    if (/^объект\s+\d+$/i.test(objectName)) {
      return {
        blocked: false,
        severity: "warning",
        message: "Название выглядит как технический fallback. Лучше указать понятное имя объекта.",
      };
    }

    return {
      blocked: false,
      severity: "ok",
      message: "Запись будет добавлена в wash_object_names.json.",
    };
  }

  function updateAddObjectDialogState(dialog) {
    if (!dialog) {
      return;
    }

    const stateInfo = getAddObjectDialogState(dialog);
    const feedback = dialog.querySelector("[data-object-editor-create-feedback]");
    const saveButton = dialog.querySelector('[data-object-editor-create-save]');
    const panel = dialog.querySelector(".object-editor-create-panel");
    if (feedback) {
      feedback.textContent = stateInfo.message;
      feedback.dataset.state = stateInfo.severity;
    }
    if (panel) {
      panel.dataset.state = stateInfo.severity;
    }
    if (saveButton) {
      saveButton.disabled = stateInfo.blocked;
    }
  }

  function openObjectEditor(initialQuery = "") {
    if (!modalRoot.hidden) {
      closeChartModal();
    }

    objectEditorRoot.hidden = false;
    syncOverlayState();
    objectEditorRoot.innerHTML = `
      <div class="object-editor-backdrop" data-close-object-editor></div>
      <section class="object-editor-panel" role="dialog" aria-modal="true" aria-label="Редактор названий объектов">
        <header class="object-editor-header">
          <div>
            <h2>Редактор объектов</h2>
            <p class="object-editor-copy">Названия объектов по каналам.</p>
          </div>
          <div class="object-editor-header-actions">
            <button type="button" class="chart-modal-icon-button chart-modal-icon-button--danger" data-close-object-editor aria-label="Закрыть редактор объектов" title="Закрыть">
              <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
                <path d="M5 5L15 15"></path>
                <path d="M15 5L5 15"></path>
              </svg>
            </button>
          </div>
        </header>
        <div class="object-editor-list" id="objectEditorList">${renderObjectEditorRows()}</div>
        <footer class="object-editor-footer">
          <button type="button" class="object-editor-toolbar-button object-editor-toolbar-button--success" data-open-add-object>Добавить объект</button>
        </footer>
        <div class="object-editor-create" data-object-editor-create hidden>
          <div class="object-editor-create-backdrop" data-close-add-object></div>
          <section class="object-editor-create-panel" role="dialog" aria-modal="true" aria-label="Добавить объект" data-state="neutral">
            <header class="object-editor-create-header">
              <div class="eyebrow">Новая запись</div>
            </header>
            <form class="object-editor-create-form" data-object-editor-add-form>
              <input type="hidden" name="channel" value="1">
              <input type="hidden" name="object_id" value="1">
              <label class="object-editor-label object-editor-label--grow">
                <span>Канал</span>
                <div class="object-editor-choice-grid object-editor-choice-grid--channels">
                  ${renderObjectEditorChannelChoices(1)}
                </div>
              </label>
              <label class="object-editor-label object-editor-label--grow">
                <span>Object ID</span>
                <div class="object-editor-choice-grid object-editor-choice-grid--ids">
                  ${renderObjectEditorIdChoices(1)}
                </div>
              </label>
              <label class="object-editor-label object-editor-label--grow">
                <span>Название</span>
                <input
                  type="text"
                  name="object_name"
                  placeholder="Название объекта"
                  autocomplete="off"
                  spellcheck="false"
                  required
                >
              </label>
              <div class="object-editor-create-feedback" data-object-editor-create-feedback data-state="ok">
                Слот свободен. Введи название объекта, чтобы добавить запись.
              </div>
              <div class="object-editor-create-actions">
                <button type="submit" class="object-editor-toolbar-button object-editor-toolbar-button--primary" data-object-editor-create-save>Сохранить</button>
                <button type="button" class="object-editor-toolbar-button object-editor-toolbar-button--danger" data-close-add-object>Отмена</button>
              </div>
            </form>
          </section>
        </div>
      </section>
    `;

    objectEditorRoot.querySelectorAll("[data-close-object-editor]").forEach((element) => {
      element.addEventListener("click", closeObjectEditor);
    });

    const listRoot = objectEditorRoot.querySelector("#objectEditorList");
    const addDialog = objectEditorRoot.querySelector("[data-object-editor-create]");
    objectEditorRoot.querySelectorAll("[data-open-add-object]").forEach((element) => {
      element.addEventListener("click", openAddObjectDialog);
    });
    objectEditorRoot.querySelectorAll("[data-close-add-object]").forEach((element) => {
      element.addEventListener("click", closeAddObjectDialog);
    });
    addDialog?.querySelector('input[name="object_name"]')?.addEventListener("input", () => {
      updateAddObjectDialogState(addDialog);
    });

    objectEditorRoot.onsubmit = async (event) => {
      event.preventDefault();
      const form = event.target.closest("form");
      if (!form) {
        return;
      }

      if (form.matches("[data-object-editor-add-form]")) {
        const formData = new FormData(form);
        const channel = Number(formData.get("channel") || 0);
        const objectId = Number(formData.get("object_id") || 0);
        const objectName = normalizeObjectName(formData.get("object_name") || "");
        const submitButton = form.querySelector('button[type="submit"]');
        const originalLabel = submitButton?.textContent || "Добавить";

        const validation = getAddObjectDialogState(addDialog);
        if (validation.blocked) {
          updateAddObjectDialogState(addDialog);
          window.alert(validation.message);
          return;
        }

        if (submitButton) {
          submitButton.disabled = true;
          submitButton.textContent = "Добавляю...";
        }

        try {
          const payload = await persistObjectName(channel, objectId, objectName, "create");
          applyObjectNameToWashData(payload.channel, payload.object_id, payload.object_name);
          if (Array.isArray(payload.object_rows)) {
            replaceObjectRows(payload.object_rows);
          }
          if (listRoot) {
            listRoot.innerHTML = renderObjectEditorRows();
          }
          closeAddObjectDialog();
        } catch (error) {
          updateAddObjectDialogState(addDialog);
          window.alert(error instanceof Error ? error.message : "Не удалось добавить объект в JSON.");
        } finally {
          if (submitButton) {
            submitButton.disabled = false;
            submitButton.textContent = originalLabel;
            updateAddObjectDialogState(addDialog);
          }
        }
        return;
      }

      if (!form.matches("[data-object-editor-form]")) {
        return;
      }

      const formData = new FormData(form);
      const channel = Number(formData.get("channel") || 0);
      const objectId = Number(formData.get("object_id") || 0);
      const objectName = normalizeObjectName(formData.get("object_name") || "");
      const submitButton = form.querySelector('button[type="submit"]');
      const originalLabel = submitButton?.textContent || "Сохранить";

      if (submitButton) {
        submitButton.disabled = true;
        submitButton.textContent = "Сохраняю...";
      }

      try {
        const payload = await persistObjectName(channel, objectId, objectName, "set");
        applyObjectNameToWashData(payload.channel, payload.object_id, payload.object_name);
        if (Array.isArray(payload.object_rows)) {
          replaceObjectRows(payload.object_rows);
        }
        if (listRoot) {
          listRoot.innerHTML = renderObjectEditorRows();
        }
      } catch (error) {
        window.alert(error instanceof Error ? error.message : "Не удалось сохранить название объекта.");
      } finally {
        if (submitButton) {
          submitButton.disabled = false;
          submitButton.textContent = originalLabel;
        }
      }
    };

    objectEditorRoot.onclick = async (event) => {
      const choiceButton = event.target.closest("[data-choice-group]");
      if (choiceButton) {
        const dialog = choiceButton.closest("[data-object-editor-create]");
        if (dialog) {
          const groupName = String(choiceButton.dataset.choiceGroup || "");
          const nextValue = String(choiceButton.dataset.choiceValue || "");
          const targetField = dialog.querySelector(`input[name="${groupName}"]`);
          if (targetField) {
            targetField.value = nextValue;
          }
          syncObjectEditorChoiceGroup(dialog, groupName, nextValue);
          updateAddObjectDialogState(dialog);
        }
        return;
      }

      const button = event.target.closest("[data-object-editor-reset]");
      if (!button) {
        return;
      }

      const channel = Number(button.dataset.channel || 0);
      const objectId = Number(button.dataset.objectId || 0);
      const objectName = String(button.dataset.objectName || "");
      const confirmed = window.confirm(`Сбросить пользовательское имя для объекта «${objectName}»?`);
      if (!confirmed) {
        return;
      }

      const originalLabel = button.textContent || "Сбросить";
      button.disabled = true;
      button.textContent = "Сбрасываю...";
      try {
        const payload = await persistObjectName(channel, objectId, "", "reset");
        applyObjectNameToWashData(payload.channel, payload.object_id, payload.object_name);
        if (Array.isArray(payload.object_rows)) {
          replaceObjectRows(payload.object_rows);
        }
        if (listRoot) {
          listRoot.innerHTML = renderObjectEditorRows();
        }
      } catch (error) {
        window.alert(error instanceof Error ? error.message : "Не удалось сбросить название объекта.");
      } finally {
        button.disabled = false;
        button.textContent = originalLabel;
      }
    };
  }

  function closeChartModal() {
    modalRequestId += 1;
    activeModalKey = "";
    activeModalChartReady = Promise.resolve(false);
    modalRoot.hidden = true;
    modalRoot.innerHTML = "";
    clearPrintMode();
    clearDetachedPrintDocument();
    syncOverlayState();
  }

  function renderModalLoading() {
    modalRoot.hidden = false;
    activeModalChartReady = Promise.resolve(false);
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
        <div class="technical-empty">Подготавливаю данные мойки и строю график.</div>
      </section>
    `;

    modalRoot.querySelectorAll("[data-close-chart-modal]").forEach((element) => {
      element.addEventListener("click", closeChartModal);
    });
  }

  async function openWashModal(key) {
    const requestId = ++modalRequestId;
    activeModalKey = key;
    renderModalLoading();

    try {
      const detail = await getDetail(key);
      const navigation = getModalNavigation(key);
      prefetchChartPayload(detail.chart_data_url);
      prefetchWashContext(navigation.previous?.key || "");
      prefetchWashContext(navigation.next?.key || "");
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
                    <td>${escapeHtml(formatModalDateTime(detail.start_time || detail.date_time))}</td>
                  </tr>
                  <tr>
                    <th scope="row">Конец мойки</th>
                    <td>${escapeHtml(formatModalDateTime(detail.end_time))}</td>
                  </tr>
                  <tr>
                    <th scope="row">Длительность мойки</th>
                    <td>${escapeHtml(detail.duration)}</td>
                  </tr>
                  <tr data-wash-result-row>
                    <th scope="row">Результат</th>
                    <td>${escapeHtml(detail.status)}</td>
                  </tr>
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
        ${renderPrintDocument(detail)}
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
          void printWashDetail(detail, "print");
        });
      }
      modalRoot.querySelectorAll("[data-open-wash-key]").forEach((element) => {
        if (!element.dataset.openWashKey) {
          return;
        }
        element.addEventListener("click", () => openWashModal(element.dataset.openWashKey));
      });

      activeModalChartReady = mountChart(modalRoot, detail, requestId);
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

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (!settingsRoot.hidden) {
        closeSettings();
        return;
      }
      if (!objectEditorRoot.hidden) {
        closeObjectEditor();
        return;
      }
      if (!modalRoot.hidden) {
        closeChartModal();
      }
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
    openWashModal(row.dataset.key);
  });
  washList.addEventListener("keydown", (event) => {
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
    openWashModal(row.dataset.key);
  });
  window.addEventListener("resize", scheduleVirtualizedWashList);

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

  [searchInput, channelFilter, sortOrder].forEach((element) => {
    if (element === searchInput) {
      element.addEventListener("input", scheduleSearchRender);
      element.addEventListener("change", () => renderWashList({ resetScroll: true }));
      return;
    }

    element.addEventListener("input", () => renderWashList({ resetScroll: true }));
    element.addEventListener("change", () => renderWashList({ resetScroll: true }));
  });

  function handleDayFilterChange() {
    state.activePeriodPreset = "";
    syncPeriodPresetButtons();
    syncDateFilterBounds();
    syncDayFilterButton();
    renderWashList({ resetScroll: true });
  }

  dayFilter.addEventListener("input", handleDayFilterChange);
  dayFilter.addEventListener("change", handleDayFilterChange);

  clearDateFiltersButton.addEventListener("click", () => {
    resetAllSearchFilters();
    renderWashList({ resetScroll: true });
  });

  if (workspaceRefreshForm) {
    workspaceRefreshForm.addEventListener("submit", async (event) => {
      event.preventDefault();

      const submitButton = workspaceRefreshForm.querySelector('button[type="submit"]');
      const originalLabel = submitButton?.textContent || "";
      if (submitButton) {
        submitButton.disabled = true;
        submitButton.textContent = "Обновляю...";
      }

      workspaceJobFeed?.ensureMonitoring?.();
      setScreenError("");

      try {
        const payload = await startWorkspaceRefresh();
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

  const openSettingsButton = document.querySelector("#openSettings");
  if (openSettingsButton) {
    openSettingsButton.addEventListener("click", () => openSettings());
  }

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
  setWashListMessage("Загружаю список моек...");
  void hydrateWorkspaceData({ resetScroll: true }).catch(() => {
    setScreenError("Не удалось загрузить список моек.");
    setWashListMessage("Не удалось загрузить список моек.");
    if (openObjectEditorButton) {
      openObjectEditorButton.disabled = false;
    }
  });
})();
