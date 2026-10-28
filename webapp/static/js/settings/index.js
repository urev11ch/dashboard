// Окно настроек: разделы (общие, FTP, график, обновления, архивы, диагностика)
// и панель обновлений в нём.

import { appState } from "../core/app-state.js";
import { apiJson } from "../core/api.js";
import { formatBytes, formatNormValue } from "../core/format.js";
import { syncOverlayState } from "../core/overlays.js";
import { runHandler, showToast } from "../core/toast.js";
import { downloadAndInstallUpdate } from "../updates.js";
import { loadDiagnosticsInto } from "./diagnostics.js";
import { fetchChartStyles, renderChartStyleControls, saveChartStyles } from "./chart-styles.js";
import { isWashResultVisible, setWashResultVisible } from "../workspace/result-visibility.js";
import { state } from "../workspace/store.js";

const { escapeHtml, isValidHexColor: isValidHexColorLike } = window.WashChart;

// Зависимости от main.js: корневой элемент окна, beforeOpen (закрыть окно мойки
// и редактор) и hydrateWorkspaceData. Задаются в initSettings.
let settingsRoot = null;
let beforeOpen = () => {};
let hydrateWorkspaceData = null;

export function initSettings(root, deps) {
  settingsRoot = root;
  ({
    beforeOpen,
    hydrateWorkspaceData,
  } = deps);
}

// Подписи результата мойки (ключи/значения по умолчанию совпадают с сервером).
const RESULT_LABEL_FIELDS = [
  { key: "completed", label: "Завершено штатно", def: "Завершено штатно" },
  { key: "check", label: "Требует проверки", def: "Требует проверки" },
];
// Разделы окна настроек (боковая навигация в стиле System Settings macOS).
const SETTINGS_PAGES = [
  { id: "general", label: "Общие", icon: "⚙" },
  { id: "ftp", label: "FTP", icon: "↻" },
  { id: "chart", label: "График", icon: "◠" },
  { id: "updates", label: "Обновления", icon: "⤓" },
  { id: "archives", label: "Архивы", icon: "▦" },
  { id: "diagnostics", label: "Диагностика", icon: "ⓘ" },
];

// Токен открытой панели: ответы запросов закрытой (или переоткрытой) панели
// уже не применятся.
let settingsOpenId = 0;
// Активный таймер подтверждения очистки архивов — чистится при закрытии настроек.
let archiveCleanupInterval = null;

export function closeSettings() {
  // Новый токен: ответы запросов открытой панели уже не применятся.
  settingsOpenId += 1;
  if (archiveCleanupInterval !== null) {
    clearInterval(archiveCleanupInterval);
    archiveCleanupInterval = null;
  }
  settingsRoot.hidden = true;
  settingsRoot.innerHTML = "";
  syncOverlayState();
}

async function fetchAppSettings() {
  const payload = await apiJson("/api/settings");
  return payload && typeof payload.settings === "object" ? payload.settings : {};
}

async function saveAppSettings(patch) {
  const payload = await apiJson("/api/settings", { method: "POST", body: { settings: patch } });
  return payload && typeof payload.settings === "object" ? payload.settings : patch;
}

export async function openSettings(initialPage = "general") {
  beforeOpen();
  const openId = ++settingsOpenId;
  settingsRoot.hidden = false;
  syncOverlayState();

  // Оба запроса параллельно. Сбой любого не мешает открыть окно: настройки
  // покажем со значениями по умолчанию, секцию стилей графика — пустой.
  const [loadedSettings, loadedChartStyles] = await Promise.all([
    fetchAppSettings().catch(() => ({})),
    fetchChartStyles().catch(() => null),
  ]);
  const settings = {
    ftp_auto_refresh_enabled: true,
    ftp_auto_refresh_minutes: 5,
    default_folder_path: "",
    ...loadedSettings,
  };
  let chartStyles = loadedChartStyles || { series: {}, defaults: [] };

  // Пользователь мог закрыть окно (или открыть заново), пока грузились
  // настройки: старый ответ не должен перетирать свежую панель.
  if (openId !== settingsOpenId || settingsRoot.hidden) {
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
      <div class="settings-macos">
        <nav class="settings-nav" role="tablist" aria-label="Разделы настроек">
          ${SETTINGS_PAGES.map((page, index) => `
            <button type="button" class="settings-nav-item${index === 0 ? " is-active" : ""}" role="tab" data-settings-nav="${page.id}" aria-selected="${index === 0 ? "true" : "false"}">
              <span class="settings-nav-icon" aria-hidden="true">${page.icon}</span>
              <span class="settings-nav-label">${escapeHtml(page.label)}</span>
            </button>
          `).join("")}
        </nav>
        <div class="settings-content">
          <section class="settings-page" data-settings-page="general">
            <h3 class="settings-page-title">Общие</h3>

            <h4 class="settings-group-title">Результат мойки</h4>
            <label class="settings-option">
              <span class="settings-option-text"><strong>Показывать результат</strong></span>
              <input type="checkbox" data-setting-wash-result ${isWashResultVisible() ? "checked" : ""}>
            </label>
            ${RESULT_LABEL_FIELDS.map((field) => `
              <div class="settings-option settings-option--stacked">
                <span class="settings-option-text"><strong>${escapeHtml(field.label)}</strong></span>
                <input type="text" class="settings-text-input" data-setting-result-label="${field.key}" maxlength="120" placeholder="${escapeHtml(field.def)}" value="${escapeHtml(resultLabels[field.key] || field.def)}" autocomplete="off" spellcheck="false">
              </div>
            `).join("")}
            <p class="settings-note">Требовать финальный шаг «Окончание мойки»: если включено, мойка без него помечается «требует проверки». Многие станции этот шаг не пишут — по умолчанию выключено.</p>
            <label class="settings-option">
              <span class="settings-option-text"><strong>Требовать шаг окончания мойки</strong></span>
              <input type="checkbox" data-setting-require-completion ${settings.require_completion_step ? "checked" : ""}>
            </label>

            <h4 class="settings-group-title">Нормативы концентрации</h4>
            <p class="settings-note">Мин. концентрация рабочего раствора за фазу сравнивается с нормативом. Ниже нормы — мойка помечается «требует проверки». Нормативы вводятся вручную (проценты), пустое поле — фаза не оценивается.</p>
            <label class="settings-option">
              <span class="settings-option-text"><strong>Оценивать концентрацию</strong></span>
              <input type="checkbox" data-setting-concentration-enabled ${settings.concentration_eval_enabled ? "checked" : ""}>
            </label>
            <label class="settings-option">
              <span class="settings-option-text"><strong>Норматив щёлочи, %</strong></span>
              <input type="number" min="0" max="100" step="0.1" data-setting-concentration-norm="alkali" placeholder="—" value="${formatNormValue(settings.concentration_norms?.alkali)}">
            </label>
            <label class="settings-option">
              <span class="settings-option-text"><strong>Норматив кислоты, %</strong></span>
              <input type="number" min="0" max="100" step="0.1" data-setting-concentration-norm="acid" placeholder="—" value="${formatNormValue(settings.concentration_norms?.acid)}">
            </label>
            <label class="settings-option">
              <span class="settings-option-text"><strong>Допуск, % (0–100)</strong></span>
              <input type="number" min="0" max="100" step="1" data-setting-concentration-tolerance value="${Number(settings.concentration_tolerance_percent) || 0}">
            </label>

            <h4 class="settings-group-title">Источник данных</h4>
            <div class="settings-option settings-option--stacked">
              <span class="settings-option-text"><strong>Папка по умолчанию</strong></span>
              <input type="text" class="settings-text-input" data-setting-default-folder placeholder="Встроенная папка datalog" value="${escapeHtml(settings.default_folder_path || "")}" autocomplete="off" spellcheck="false">
            </div>
          </section>
          <section class="settings-page" data-settings-page="ftp" hidden>
            <h3 class="settings-page-title">FTP</h3>
            <label class="settings-option">
              <span class="settings-option-text"><strong>Автообновление включено</strong></span>
              <input type="checkbox" data-setting-ftp-auto-refresh ${settings.ftp_auto_refresh_enabled ? "checked" : ""}>
            </label>
            <label class="settings-option">
              <span class="settings-option-text"><strong>Интервал, мин</strong></span>
              <input type="number" min="1" max="1440" step="1" data-setting-ftp-auto-refresh-minutes value="${Number(settings.ftp_auto_refresh_minutes) || 5}">
            </label>
          </section>
          <section class="settings-page" data-settings-page="chart" hidden>
            <h3 class="settings-page-title">График</h3>
            <div class="settings-option settings-option--stacked">
              <span class="settings-option-text"><strong>Цвета и линии</strong></span>
              <div class="settings-chart-grid" data-chart-style-grid>${renderChartStyleControls(chartStyles.defaults, chartStyles.series)}</div>
              <div class="settings-chart-actions">
                <button type="button" class="ghost" data-chart-style-reset>Сбросить</button>
              </div>
            </div>
            <div class="settings-option settings-option--stacked">
              <span class="settings-option-text"><strong>Кэш графиков</strong></span>
              <p class="settings-note">Сбрасывает сохранённые графики — они соберутся заново при следующем открытии. Помогает, если график отображается в устаревшем виде.</p>
              <div class="settings-chart-actions">
                <button type="button" class="ghost" data-chart-cache-clear>Очистить кэш графиков</button>
              </div>
            </div>
          </section>
          <section class="settings-page" data-settings-page="updates" hidden>
            <h3 class="settings-page-title">Обновления и автозапуск</h3>
            <div class="settings-option">
              <span class="settings-option-text"><strong>Проверка обновлений</strong><span class="settings-option-hint">Сверяет текущую версию ${escapeHtml(appState.appVersion || "")} с последним релизом на GitHub</span></span>
              <button type="button" class="ghost" data-check-updates>Проверить</button>
            </div>
            <label class="settings-option">
              <span class="settings-option-text"><strong>Обновляться автоматически</strong><span class="settings-option-hint">При запуске приложение само скачает и установит новую версию и перезапустится. Windows один раз спросит разрешение на установку.</span></span>
              <input type="checkbox" data-setting-auto-update ${settings.auto_update_enabled ? "checked" : ""}>
            </label>
            <label class="settings-option">
              <span class="settings-option-text"><strong>Автозапуск с Windows</strong></span>
              <input type="checkbox" data-setting-autostart ${settings.autostart ? "checked" : ""}>
            </label>
            <!-- Панель обновления: заполняется renderUpdatePanel() по данным
                 /api/update-check. Пока обновления нет — остаётся пустой. -->
            <div class="update-panel" data-update-panel hidden></div>
          </section>
          <section class="settings-page" data-settings-page="archives" hidden>
            <h3 class="settings-page-title">Архивы</h3>
            <label class="settings-option">
              <span class="settings-option-text"><strong>Автоочистка архивов</strong></span>
              <input type="checkbox" data-setting-archive-retention ${settings.archive_retention_enabled ? "checked" : ""}>
            </label>
            <label class="settings-option">
              <span class="settings-option-text"><strong>Хранить, дней (1–730)</strong></span>
              <input type="number" min="1" max="730" step="1" data-setting-retention-days value="${Number(settings.archive_retention_days) || 365}">
            </label>
            <div class="settings-chart-actions">
              <button type="button" class="ghost" data-archive-cleanup-now>Очистить сейчас</button>
            </div>
            <div class="archive-confirm" data-archive-confirm hidden>
              <p class="settings-note" data-archive-confirm-text></p>
              <div class="settings-chart-actions">
                <button type="button" class="ghost" data-archive-confirm-cancel>Отмена</button>
                <button type="button" data-archive-confirm-ok disabled>Очистить</button>
              </div>
            </div>
          </section>
          <section class="settings-page" data-settings-page="diagnostics" hidden>
            <h3 class="settings-page-title">Диагностика</h3>
            <div class="diagnostics-body" data-diagnostics-body>
              <div class="technical-empty">Загрузка…</div>
            </div>
          </section>
        </div>
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
      showToast("Настройки сохранены", "success");
    } catch (_error) {
      showToast("Не удалось сохранить настройки.", "error");
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
    defaultFolderInput.addEventListener("change", async () => {
      // Через захваченную defaultFolderInput, а не event.currentTarget: он
      // обнуляется по завершении диспетчеризации, и после await обращение к
      // нему бросало бы TypeError → ложный тост «Не удалось сохранить».
      try {
        const saved = await saveAppSettings({
          default_folder_path: String(defaultFolderInput.value || "").trim(),
        });
        if (saved && typeof saved.default_folder_path === "string") {
          defaultFolderInput.value = saved.default_folder_path;
        }
        showToast("Настройки сохранены", "success");
      } catch (_error) {
        showToast("Не удалось сохранить настройки.", "error");
      }
    });
  }

  const chartStyleGrid = settingsRoot.querySelector("[data-chart-style-grid]");
  const chartStyleReset = settingsRoot.querySelector("[data-chart-style-reset]");

  let chartStyleDefaults = new Map(chartStyles.defaults.map((def) => [def.id, def]));

  // Шлём только отличия от дефолтов (как wash-chart.js при сохранении из
  // графика): иначе дефолты всех серий превращаются в вечные оверрайды на
  // сервере и смена палитры в SERIES_CONFIG перестаёт применяться.
  const collectChartStyleSeries = () => {
    const series = {};
    if (!chartStyleGrid) {
      return series;
    }
    chartStyleGrid.querySelectorAll("[data-chart-style-color]").forEach((input) => {
      const id = input.dataset.chartStyleColor;
      const def = chartStyleDefaults.get(id) || {};
      const lineSelect = chartStyleGrid.querySelector(`[data-chart-style-line="${id}"]`);
      const color = String(input.value || "");
      const lineStyle = lineSelect ? String(lineSelect.value || "") : "";
      const entry = {};

      if (
        isValidHexColorLike(color) &&
        color.toLowerCase() !== String(def.color || "").toLowerCase()
      ) {
        entry.color = color;
      }
      if (lineStyle && lineStyle !== String(def.lineStyle || "solid")) {
        entry.lineStyle = lineStyle;
      }
      if (Object.keys(entry).length) {
        series[id] = entry;
      }
    });
    return series;
  };

  const persistChartStyles = async () => {
    try {
      const saved = await saveChartStyles(collectChartStyleSeries());
      // Применяем к кэшу графиков, чтобы следующий открытый график сразу учёл изменения.
      window.WashChart?.setSeriesStyles?.(saved);
      showToast("Оформление графика сохранено", "success");
    } catch (_error) {
      showToast("Не удалось сохранить оформление графика.", "error");
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
        if (openId !== settingsOpenId) {
          return;
        }
        chartStyleDefaults = new Map(fresh.defaults.map((def) => [def.id, def]));
        if (chartStyleGrid) {
          chartStyleGrid.innerHTML = renderChartStyleControls(fresh.defaults, fresh.series);
        }
        window.WashChart?.setSeriesStyles?.(fresh.series);
        showToast("Оформление графика сброшено", "success");
      } catch (_error) {
        if (openId !== settingsOpenId) {
          return;
        }
        showToast("Не удалось сбросить оформление графика.", "error");
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
      showToast("Настройки сохранены", "success");
    } catch (_error) {
      showToast("Не удалось сохранить настройки.", "error");
    }
  };
  resultLabelInputs.forEach((input) => {
    input.addEventListener("change", persistResultLabels);
  });

  const requireCompletionToggle = settingsRoot.querySelector("[data-setting-require-completion]");
  if (requireCompletionToggle) {
    requireCompletionToggle.addEventListener("change", async (event) => {
      try {
        await saveAppSettings({ require_completion_step: Boolean(event.currentTarget.checked) });
        // Пересчёт статуса мойки — на чтении, поэтому обновляем список сразу.
        if (appState.hasWorkspace) {
          hydrateWorkspaceData({ resetScroll: false }).catch(() => {});
        }
        showToast("Настройки сохранены", "success");
      } catch (_error) {
        showToast("Не удалось сохранить настройки.", "error");
      }
    });
  }

  // Проверка обновлений — явное действие: одна проверка на нажатие. Кнопку
  // блокируем на время запроса, иначе несколько кликов подряд наплодят
  // параллельных походов к GitHub.
  const checkUpdatesButton = settingsRoot.querySelector("[data-check-updates]");
  if (checkUpdatesButton) {
    checkUpdatesButton.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      button.textContent = "Проверяю…";
      try {
        await checkForUpdates();
      } finally {
        button.disabled = false;
        button.textContent = "Проверить";
      }
    });
  }

  // Панель обновления перерисовывается целиком на каждом шаге, поэтому клик
  // по кнопке установки — делегированный.
  renderUpdatePanel();
  const updatePanel = settingsRoot.querySelector("[data-update-panel]");
  if (updatePanel) {
    updatePanel.addEventListener("click", (event) => {
      const button = event.target.closest("[data-update-install]");
      if (!button) {
        return;
      }
      button.disabled = true;
      runHandler(startUpdateInstall());
    });
  }

  const autoUpdateToggle = settingsRoot.querySelector("[data-setting-auto-update]");
  if (autoUpdateToggle) {
    autoUpdateToggle.addEventListener("change", async (event) => {
      const enabled = Boolean(event.currentTarget.checked);
      try {
        await saveAppSettings({ auto_update_enabled: enabled });
        showToast(
          enabled ? "Обновления будут ставиться автоматически" : "Автообновление выключено",
          "success"
        );
      } catch (_error) {
        showToast("Не удалось сохранить настройку обновлений.", "error");
      }
    });
  }

  const autostartToggle = settingsRoot.querySelector("[data-setting-autostart]");
  if (autostartToggle) {
    autostartToggle.addEventListener("change", async (event) => {
      const enabled = Boolean(event.currentTarget.checked);
      try {
        await saveAppSettings({ autostart: enabled });
        const api = window.pywebview && window.pywebview.api;
        if (api && typeof api.set_autostart === "function") {
          const result = await api.set_autostart({ enabled });
          if (result && result.supported === false) {
            showToast("Автозапуск доступен только в приложении Windows.", "info");
          } else if (result && result.ok) {
            showToast(enabled ? "Автозапуск включён" : "Автозапуск выключен", "success");
          } else {
            showToast("Не удалось изменить автозапуск.", "error");
          }
        } else {
          showToast("Автозапуск доступен только в приложении Windows.", "info");
        }
      } catch (_error) {
        showToast("Не удалось изменить автозапуск.", "error");
      }
    });
  }

  const retentionToggle = settingsRoot.querySelector("[data-setting-archive-retention]");
  const retentionDays = settingsRoot.querySelector("[data-setting-retention-days]");
  // Одно приведение для сохранения и для очистки: срок в подтверждении обязан
  // совпадать с тем, по которому сервер удаляет.
  const retentionDaysValue = () => {
    const daysRaw = Number(retentionDays?.value);
    return Number.isFinite(daysRaw) && retentionDays?.value !== ""
      ? Math.min(730, Math.max(1, Math.round(daysRaw)))
      : 365;
  };
  const persistRetention = async () => {
    const days = retentionDaysValue();
    try {
      const saved = await saveAppSettings({
        archive_retention_enabled: Boolean(retentionToggle?.checked),
        archive_retention_days: days,
      });
      if (retentionDays && saved && Number.isFinite(Number(saved.archive_retention_days))) {
        retentionDays.value = Number(saved.archive_retention_days);
      }
      showToast("Настройки сохранены", "success");
    } catch (_error) {
      showToast("Не удалось сохранить настройки.", "error");
    }
  };
  if (retentionToggle) {
    retentionToggle.addEventListener("change", persistRetention);
  }
  if (retentionDays) {
    retentionDays.addEventListener("change", persistRetention);
  }

  // Очистка архивов — необратимое удаление, поэтому подтверждение с обратным
  // отсчётом: кнопка «Очистить» разблокируется только через 30 секунд (защита
  // от случайного нажатия), «Отмена» доступна всегда.
  const cleanupButton = settingsRoot.querySelector("[data-archive-cleanup-now]");
  const cleanupConfirm = settingsRoot.querySelector("[data-archive-confirm]");
  const cleanupConfirmText = settingsRoot.querySelector("[data-archive-confirm-text]");
  const cleanupConfirmOk = settingsRoot.querySelector("[data-archive-confirm-ok]");
  const cleanupConfirmCancel = settingsRoot.querySelector("[data-archive-confirm-cancel]");
  const CLEANUP_DELAY_SECONDS = 30;

  const stopCleanupTimer = () => {
    if (archiveCleanupInterval !== null) {
      clearInterval(archiveCleanupInterval);
      archiveCleanupInterval = null;
    }
  };

  const hideCleanupConfirm = () => {
    stopCleanupTimer();
    if (cleanupConfirm) cleanupConfirm.hidden = true;
    if (cleanupButton) cleanupButton.hidden = false;
  };

  if (cleanupButton && cleanupConfirm && cleanupConfirmOk) {
    let cleanupDays = 365; // срок, показанный в открытом подтверждении
    cleanupButton.addEventListener("click", () => {
      const days = retentionDaysValue();
      cleanupDays = days;
      if (cleanupConfirmText) {
        cleanupConfirmText.textContent =
          `Удалить архивы старше ${days} дней? Эти мойки исчезнут из журнала, ` +
          `а удалённые файлы не будут скачиваться с панели снова (до смены настроек хранения). ` +
          `Действие необратимо — кнопка «Очистить» станет доступна через ${CLEANUP_DELAY_SECONDS} с.`;
      }
      cleanupButton.hidden = true;
      cleanupConfirm.hidden = false;

      let remaining = CLEANUP_DELAY_SECONDS;
      cleanupConfirmOk.disabled = true;
      cleanupConfirmOk.textContent = `Очистить (${remaining})`;
      stopCleanupTimer();
      archiveCleanupInterval = setInterval(() => {
        remaining -= 1;
        if (remaining > 0) {
          cleanupConfirmOk.textContent = `Очистить (${remaining})`;
        } else {
          stopCleanupTimer();
          cleanupConfirmOk.disabled = false;
          cleanupConfirmOk.textContent = "Очистить";
        }
      }, 1000);
    });

    if (cleanupConfirmCancel) {
      cleanupConfirmCancel.addEventListener("click", hideCleanupConfirm);
    }

    cleanupConfirmOk.addEventListener("click", async () => {
      if (cleanupConfirmOk.disabled) {
        return;
      }
      stopCleanupTimer();
      cleanupConfirmOk.disabled = true;
      try {
        const data = await apiJson("/api/archives/cleanup", {
          method: "POST",
          body: { days: cleanupDays },
          errorMessage: "Не удалось выполнить очистку.",
        });
        showToast(
          `Удалено файлов: ${data.removed || 0}, освобождено ${formatBytes(data.freed_bytes || 0)}`,
          "success"
        );
      } catch (error) {
        // 409 «идёт обработка» — штатный отказ: причина важнее общего текста.
        showToast(error?.message || "Не удалось выполнить очистку.", "error");
      } finally {
        hideCleanupConfirm();
      }
    });
  }

  // Нормативы концентрации: тумблер + два норматива + допуск. Пустое поле
  // норматива = не задан (шлём null). Обновляем список, чтобы вердикт применился.
  const concentrationEnabled = settingsRoot.querySelector("[data-setting-concentration-enabled]");
  const concentrationNorms = settingsRoot.querySelectorAll("[data-setting-concentration-norm]");
  const concentrationTolerance = settingsRoot.querySelector("[data-setting-concentration-tolerance]");
  const persistConcentration = async () => {
    const norms = {};
    concentrationNorms.forEach((input) => {
      const raw = String(input.value ?? "").trim();
      if (raw === "") {
        norms[input.dataset.settingConcentrationNorm] = null;
      } else {
        const num = Number(raw);
        norms[input.dataset.settingConcentrationNorm] = Number.isFinite(num)
          ? Math.min(100, Math.max(0, num))
          : null;
      }
    });
    const tolRaw = Number(concentrationTolerance?.value);
    const tolerance = Number.isFinite(tolRaw) ? Math.min(100, Math.max(0, tolRaw)) : 0;
    try {
      const saved = await saveAppSettings({
        concentration_eval_enabled: Boolean(concentrationEnabled?.checked),
        concentration_norms: norms,
        concentration_tolerance_percent: tolerance,
      });
      // Возвращаем нормализованные сервером значения в поля.
      if (saved && saved.concentration_norms) {
        concentrationNorms.forEach((input) => {
          input.value = formatNormValue(saved.concentration_norms[input.dataset.settingConcentrationNorm]);
        });
      }
      if (concentrationTolerance && saved && Number.isFinite(Number(saved.concentration_tolerance_percent))) {
        concentrationTolerance.value = Number(saved.concentration_tolerance_percent);
      }
      if (appState.hasWorkspace) {
        hydrateWorkspaceData({ resetScroll: false }).catch(() => {});
      }
    } catch (_error) {
      showToast("Не удалось сохранить настройки.", "error");
    }
  };
  if (concentrationEnabled) {
    concentrationEnabled.addEventListener("change", persistConcentration);
  }
  concentrationNorms.forEach((input) => input.addEventListener("change", persistConcentration));
  if (concentrationTolerance) {
    concentrationTolerance.addEventListener("change", persistConcentration);
  }

  // Кнопка очистки кэша графиков.
  const chartCacheClear = settingsRoot.querySelector("[data-chart-cache-clear]");
  if (chartCacheClear) {
    chartCacheClear.addEventListener("click", async () => {
      chartCacheClear.disabled = true;
      try {
        const data = await apiJson("/api/chart-cache/clear", { method: "POST" });
        showToast(`Кэш графиков очищен (файлов: ${data.removed || 0}).`, "success");
      } catch (_error) {
        showToast("Не удалось очистить кэш графиков.", "error");
      } finally {
        chartCacheClear.disabled = false;
      }
    });
  }

  // Боковая навигация разделов (macOS-стиль): переключение активной панели.
  let diagnosticsLoaded = false;
  const showSettingsPage = (pageId) => {
    settingsRoot.querySelectorAll("[data-settings-nav]").forEach((button) => {
      const active = button.dataset.settingsNav === pageId;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
    });
    settingsRoot.querySelectorAll("[data-settings-page]").forEach((panel) => {
      panel.hidden = panel.dataset.settingsPage !== pageId;
    });
    if (pageId === "diagnostics" && !diagnosticsLoaded) {
      diagnosticsLoaded = true;
      void loadDiagnosticsInto(settingsRoot.querySelector("[data-diagnostics-body]"));
    }
  };
  settingsRoot.querySelectorAll("[data-settings-nav]").forEach((button) => {
    button.addEventListener("click", () => showSettingsPage(button.dataset.settingsNav));
  });
  if (SETTINGS_PAGES.some((page) => page.id === initialPage)) {
    showSettingsPage(initialPage);
  }
}

// Вызывается только кнопкой в настройках — одна проверка на нажатие.
// Результат всегда озвучиваем: пользователь нажал и ждёт ответа.
async function checkForUpdates() {
  try {
    const data = await apiJson("/api/update-check", { timeout: 15000 });
    state.updateInfo = data;
    renderUpdatePanel();
    if (data.update_available) {
      // Куда вести — зависит от того, умеем ли ставить сами: в десктопе это
      // кнопка в настройках, в браузере остаётся страница релизов.
      const where = canInstallUpdate()
        ? "Установить можно ниже."
        : "Смотрите GitHub Releases.";
      showToast(`Доступно обновление ${data.latest}. ${where}`, "info", 8000);
    } else if (data.latest) {
      showToast("Установлена последняя версия.", "success");
    } else {
      // Пустой latest — это «не выяснили» (нет сети/релизов), а не «актуально»:
      // бэкенд глушит ошибки запроса к GitHub и отдаёт "". Врать про
      // актуальность нельзя — пользователь пропустит важное обновление.
      showToast("Не удалось проверить обновления.", "info");
    }
  } catch (_error) {
    showToast("Не удалось проверить обновления.", "error");
  }
}

// ---- Установка обновления в один клик ----------------------------------
// Доступна только в десктоп-сборке под Windows: ставит .exe-установщик, а
// закрыть окно и перезапуститься умеет лишь мост pywebview. В браузере
// (и на не-Windows) панель показывает обычную ссылку на Releases.
function canInstallUpdate() {
  const info = state.updateInfo;
  return Boolean(
    info &&
      info.installable &&
      typeof window.pywebview?.api?.install_update === "function",
  );
}

function renderUpdatePanel() {
  const panel = document.querySelector("[data-update-panel]");
  if (!panel) {
    return;
  }
  const info = state.updateInfo;
  if (!info || !info.update_available) {
    panel.hidden = true;
    panel.innerHTML = "";
    return;
  }

  panel.hidden = false;
  const job = state.updateJob;
  const version = escapeHtml(String(info.latest || ""));

  if (job && job.status === "running") {
    const pct = job.total ? Math.round((job.downloaded / job.total) * 100) : 0;
    const label =
      job.phase === "verify"
        ? "Проверка контрольной суммы…"
        : `Скачивание ${formatBytes(job.downloaded)} из ${formatBytes(job.total)}`;
    panel.innerHTML = `
      <div class="update-panel-title">Обновление ${version}</div>
      <div class="update-progress"><div class="update-progress-bar" style="width: ${pct}%"></div></div>
      <p class="settings-note">${escapeHtml(label)}</p>
    `;
    return;
  }

  if (job && job.status === "error") {
    panel.innerHTML = `
      <div class="update-panel-title">Обновление ${version}</div>
      <p class="settings-note update-panel-error">${escapeHtml(job.error || "Не удалось скачать обновление.")}</p>
      <div class="settings-chart-actions">
        <button type="button" class="ghost" data-update-install>Повторить</button>
      </div>
    `;
    return;
  }

  if (canInstallUpdate()) {
    panel.innerHTML = `
      <div class="update-panel-title">Доступно обновление ${version}</div>
      <p class="settings-note">Приложение скачает установщик, проверит его и закроется — Windows запросит подтверждение. После установки окно откроется снова.</p>
      <div class="settings-chart-actions">
        <button type="button" data-update-install>Установить обновление</button>
      </div>
    `;
    return;
  }

  panel.innerHTML = `
    <div class="update-panel-title">Доступно обновление ${version}</div>
    <p class="settings-note">Скачайте установщик со страницы релизов: <a href="${escapeHtml(info.url || "")}" target="_blank" rel="noopener noreferrer">GitHub Releases</a>.</p>
  `;
}

async function startUpdateInstall() {
  // Повторный вход запрещён: бэкенд отбивает второй POST только пока job в
  // "running", а после "ready" — качает установщик заново и поднимает второй
  // воркер. Кнопка от этого не защищает: renderUpdatePanel на каждом опросе
  // вставляет свежую, уже включённую.
  if (state.updateBusy) {
    return;
  }
  state.updateBusy = true;
  // Токен открытой панели настроек: если её закрыли во время опроса,
  // прекращаем поллер (иначе он тикал бы 2 раза/с невидимо до ~20 мин).
  // Серверное скачивание при этом НЕ прерываем — оно продолжится и подхватится
  // повторным «Установить» (бэкенд вернёт готовую задачу).
  const settingsOpenAtStart = settingsOpenId;
  try {
    const installed = await downloadAndInstallUpdate({
      onJob: (job) => {
        state.updateJob = job;
        renderUpdatePanel();
      },
      isCancelled: () => settingsOpenId !== settingsOpenAtStart,
    });
    if (installed) {
      showToast("Запускаю установку — приложение закроется.", "info", 8000);
    }
  } catch (error) {
    // Любой сбой (таймаут fetch, обрыв, не-200, отказ от UAC) обязан
    // перевести панель в "error": ветка "running" рисует прогресс-бар БЕЗ
    // кнопок, и панель залипала бы в «Скачивание…» до перезагрузки окна.
    const message = String(error.message || error);
    state.updateJob = { status: "error", error: message };
    renderUpdatePanel();
    showToast(message, "error", 8000);
  } finally {
    state.updateBusy = false;
  }
}
