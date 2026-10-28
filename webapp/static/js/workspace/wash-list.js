// Журнал моек: элементы панели фильтров, периоды и даты, фильтрация и
// сортировка, виртуализированная отрисовка списка, поиск с задержкой.

import {
  badgeClass,
  formatDateTime,
  formatDayHeader,
  getLocalDateKey,
  getServerDayStartTs,
} from "../core/format.js";
import {
  DEFAULT_PERIOD_PRESET,
  PERIOD_PRESET_DAYS,
  buildSearchBlob,
  buildWashObjectKey,
  channelLabelFor,
  channelsFromRows,
  state,
} from "./store.js";
import { detailCache } from "./cache.js";

const { escapeHtml } = window.WashChart;

export const washList = document.querySelector("#washList");
export const washFilterCount = document.querySelector("#washFilterCount");
export const searchInput = document.querySelector("#searchInput");
export const dayFilter = document.querySelector("#dayFilter");
export const openDayFilterButton = document.querySelector("#openDayFilter");
export const channelFilter = document.querySelector("#channelFilter");
export const sortOrder = document.querySelector("#sortOrder");

// Стартовые высоты строки/заголовка дня (см. .wash-row / .wash-day-header в
// style.css). Реальные значения зависят от вёрстки и меряются по DOM после
// первой отрисовки — константы нужны только до первого измерения.
const WASH_LIST_ROW_HEIGHT_FALLBACK = 69;
const WASH_LIST_HEADER_HEIGHT_FALLBACK = 38;
// Ниже этой ширины строка списка становится резиновой (height: auto), поэтому
// виртуализация с фиксированной высотой спейсеров там неприменима.
const WASH_LIST_FLUID_LAYOUT_QUERY = "(max-width: 1100px)";
const WASH_LIST_OVERSCAN = 8;
const SEARCH_INPUT_DEBOUNCE_MS = 180;

export const washListMetrics = {
  rowHeight: WASH_LIST_ROW_HEIGHT_FALLBACK,
  headerHeight: WASH_LIST_HEADER_HEIGHT_FALLBACK,
};
export const fluidWashListQuery = window.matchMedia(WASH_LIST_FLUID_LAYOUT_QUERY);

export function isWashListVirtualized() {
  return !fluidWashListQuery.matches;
}

let washListRenderFrame = 0;
let searchRenderTimer = 0;

// Группа кнопок-переключателей: активна та, чей data-атрибут равен value.
function markActiveButton(dataKey, value) {
  const attribute = `data-${dataKey.replace(/[A-Z]/g, (char) => `-${char.toLowerCase()}`)}`;
  document.querySelectorAll(`[${attribute}]`).forEach((button) => {
    const isActive = String(button.dataset[dataKey] || "") === String(value || "");
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-pressed", isActive ? "true" : "false");
  });
}

export function syncSortButtons() {
  markActiveButton("sortValue", sortOrder.value || "date_desc");
}

function syncChannelButtons() {
  markActiveButton("channelValue", channelFilter.value);
}

// Ключ дня (YYYY-MM-DD) для «сейчас» по часам СЕРВЕРА — они же часы панели.
// start_day строк — день по часам панели (format_day_key). В вебе зоны браузера
// и сервера могут не совпадать — тогда «Сегодня» пустел или ехал на сутки.
// Если сервер не отдал tz_offset_min, считаем по зоне браузера, как раньше.
// Только для настоящего времени (Date.now()), не для меток архива.
function getServerDateKey(value = new Date()) {
  const date = value instanceof Date ? value : new Date(value);
  if (state.serverTzOffsetMin === null) {
    return getLocalDateKey(date);
  }

  const shifted = new Date(date.getTime() + state.serverTzOffsetMin * 60000);
  const year = String(shifted.getUTCFullYear());
  const month = String(shifted.getUTCMonth() + 1).padStart(2, "0");
  const day = String(shifted.getUTCDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function getPeriodPresetStartTs() {
  if (state.activePeriodPreset === "all" || !state.activePeriodPreset) {
    return null;
  }
  if (state.activePeriodPreset === "today") {
    return getServerDayStartTs(getServerDateKey());
  }
  // «7 дней» — это 7 календарных суток, включая сегодняшние, а не скользящее
  // окно ровно в 168 часов: иначе в 15:00 мойка, начавшаяся семь дней назад
  // утром, из списка выпадала («вчера была, сегодня пропала»).
  const presetDays = PERIOD_PRESET_DAYS[state.activePeriodPreset];
  if (presetDays) {
    const firstDayKey = getServerDateKey(Date.now() - (presetDays - 1) * 24 * 60 * 60 * 1000);
    return getServerDayStartTs(firstDayKey);
  }
  return null;
}

export function syncPeriodPresetButtons() {
  markActiveButton("periodPreset", state.activePeriodPreset);
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

export function syncDateFilterBounds() {
  const { min, max } = getAvailableDateBounds();
  dayFilter.min = min;
  dayFilter.max = max;
}

export function syncDayFilterButton() {
  const hasSelectedDay = Boolean(dayFilter.value);
  openDayFilterButton.classList.toggle("is-active", hasSelectedDay);
  const label = hasSelectedDay ? `Выбрана дата: ${dayFilter.value}` : "Открыть календарь";
  openDayFilterButton.title = label;
  openDayFilterButton.setAttribute("aria-label", label);
}

export function resetAllSearchFilters() {
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

export function getModalNavigation(key) {
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

export function fillChannelFilter() {
  const channelOptionsRoot = document.querySelector("#channelOptions");
  if (!channelOptionsRoot) {
    return;
  }

  const channels = channelsFromRows(state.washRows);

  if (channelFilter.value && !channels.includes(Number(channelFilter.value))) {
    channelFilter.value = "";
  }

  const buttons = [
    '<button type="button" class="toolbar-channel-option" data-channel-value="">Все</button>',
    ...channels.map(
      (channel) =>
        `<button type="button" class="toolbar-channel-option" data-channel-value="${channel}">${escapeHtml(
          channelLabelFor(channel)
        )}</button>`
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
      return String(left.object || "").localeCompare(String(right.object || ""), "ru") || rightStartTs - leftStartTs;
    }

    if (mode === "duration_desc") {
      return Number(right.duration_seconds || 0) - Number(left.duration_seconds || 0) || rightStartTs - leftStartTs;
    }

    return rightStartTs - leftStartTs;
  });

  return sorted;
}

export function getFilteredRows() {
  const query = searchInput.value.trim().toLowerCase();
  const selectedDay = dayFilter.value;
  const channel = channelFilter.value;
  const presetStartTs = getPeriodPresetStartTs();
  const todayKey = getServerDateKey();

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
  const dateTime = escapeHtml(formatDateTime(row.date_time));
  return `
    <div class="wash-row" data-key="${escapeHtml(row.key)}" role="button" tabindex="0">
      <div class="wash-cell wash-cell--primary">
        <div class="wash-entry-time" title="${dateTime}">${dateTime}</div>
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
        <span class="${badgeClass(row.status, row.result_kind)}" title="${escapeHtml(row.status)}">${escapeHtml(row.status)}</span>
        ${row.concentration_kind === "low" ? '<span class="conc-chip conc-chip--low" title="Концентрация раствора ниже нормы">конц. ↓</span>' : ""}
        <button type="button" class="wash-row-pdf-button" data-download-row-pdf title="PDF" aria-label="PDF">PDF</button>
      </div>
    </div>
  `;
}

// Границы последнего отрисованного окна. Пока они не изменились, DOM не
// трогаем: раньше innerHTML переписывался на каждом кадре скролла (полный
// reparse видимых строк — слетали hover и выделение текста).
const renderedWindow = { version: -1, start: -1, end: -1, virtualized: false };

export function invalidateRenderedWashWindow() {
  renderedWindow.version = -1;
  renderedWindow.start = -1;
  renderedWindow.end = -1;
  renderedWindow.virtualized = false;
  // Высоты строки/заголовка меряем из DOM заново после инвалидации окна
  // (resize, смена fluid-режима, опустошение списка) — но НЕ на каждом кадре
  // скролла. Высоты зависят от вёрстки/медиа-запросов, а не от содержимого,
  // поэтому между инвалидациями их можно не перемерять (см. FP1).
  washListMetricsMeasured = false;
}

function renderWashItemsHtml(startIndex, endIndex) {
  const items = state.displayItems;
  const chunks = [];
  for (let i = startIndex; i < endIndex; i += 1) {
    const item = items[i];
    chunks.push(
      item.type === "header"
        ? `<div class="wash-day-header" aria-hidden="true">${escapeHtml(item.label)}</div>`
        : renderWashRow(item.row)
    );
  }
  return chunks.join("");
}

function writeWashListHtml(html) {
  // innerHTML уничтожает сфокусированную строку — запоминаем её ключ и после
  // перерисовки возвращаем фокус клавиатуры на ту же строку, если она в DOM.
  const activeElement = document.activeElement;
  const focusedRowKey =
    activeElement && washList.contains(activeElement)
      ? activeElement.closest("[data-key]")?.dataset.key || ""
      : "";

  washList.innerHTML = html;

  if (focusedRowKey) {
    const rowToFocus = Array.from(washList.querySelectorAll("[data-key]")).find(
      (row) => row.dataset.key === focusedRowKey
    );
    rowToFocus?.focus({ preventScroll: true });
  }
}

// Реальные высоты строки и заголовка дня берём из DOM: они заданы в CSS и
// меняются вместе с медиа-запросами/настройками, а не константой в JS.
function syncWashListMetricsFromDom() {
  let changed = false;

  const rowElement = washList.querySelector(".wash-row:not(.wash-row--skeleton)");
  if (rowElement) {
    const height = rowElement.getBoundingClientRect().height;
    if (height > 0 && Math.abs(height - washListMetrics.rowHeight) > 0.5) {
      washListMetrics.rowHeight = height;
      changed = true;
    }
  }

  const headerElement = washList.querySelector(".wash-day-header");
  if (headerElement) {
    const height = headerElement.getBoundingClientRect().height;
    if (height > 0 && Math.abs(height - washListMetrics.headerHeight) > 0.5) {
      washListMetrics.headerHeight = height;
      changed = true;
    }
  }

  return changed;
}

let washListMetricsResyncing = false;
// Померены ли высоты из DOM для текущей вёрстки. Сбрасывается в
// invalidateRenderedWashWindow; пока true — при скролле getBoundingClientRect
// не дёргается (нет форс-reflow на каждом кадре).
let washListMetricsMeasured = false;

export function renderVirtualizedWashList({ resetScroll = false } = {}) {
  if (resetScroll) {
    washList.scrollTop = 0;
  }

  const items = state.displayItems;
  if (!items.length) {
    writeWashListHtml('<div class="technical-empty">Мойки не найдены</div>');
    invalidateRenderedWashWindow();
    return;
  }

  // Ниже 1100px строка резиновая (height: auto, min-height: 69px), высота
  // заранее неизвестна и у разных строк разная — спейсеры с фиксированной
  // высотой давали бы провалы и недолистывание. В этом режиме виртуализацию
  // отключаем и рисуем список целиком.
  if (!isWashListVirtualized()) {
    if (
      renderedWindow.virtualized ||
      renderedWindow.version !== state.displayVersion ||
      renderedWindow.start !== 0 ||
      renderedWindow.end !== items.length
    ) {
      writeWashListHtml(renderWashItemsHtml(0, items.length));
      renderedWindow.version = state.displayVersion;
      renderedWindow.start = 0;
      renderedWindow.end = items.length;
      renderedWindow.virtualized = false;
    }
    return;
  }

  const offsets = state.displayOffsets;
  const totalHeight = state.displayTotalHeight;
  const rowHeight = washListMetrics.rowHeight;
  const viewportHeight = Math.max(washList.clientHeight, rowHeight);
  const scrollTop = washList.scrollTop;
  const overscanPx = WASH_LIST_OVERSCAN * rowHeight;
  const windowTop = Math.max(0, scrollTop - overscanPx);
  const windowBottom = scrollTop + viewportHeight + overscanPx;

  const startIndex = findFirstVisibleItem(offsets, windowTop);
  let endIndex = startIndex;
  while (endIndex < items.length && offsets[endIndex] < windowBottom) {
    endIndex += 1;
  }

  if (
    renderedWindow.virtualized &&
    renderedWindow.version === state.displayVersion &&
    renderedWindow.start === startIndex &&
    renderedWindow.end === endIndex
  ) {
    return;
  }

  const topSpacerHeight = offsets[startIndex];
  const bottomSpacerHeight = Math.max(0, totalHeight - offsets[endIndex]);

  const chunks = [];
  if (topSpacerHeight) {
    chunks.push(`<div class="wash-list-spacer" style="height:${topSpacerHeight}px" aria-hidden="true"></div>`);
  }
  chunks.push(renderWashItemsHtml(startIndex, endIndex));
  if (bottomSpacerHeight) {
    chunks.push(`<div class="wash-list-spacer" style="height:${bottomSpacerHeight}px" aria-hidden="true"></div>`);
  }

  writeWashListHtml(chunks.join(""));
  renderedWindow.version = state.displayVersion;
  renderedWindow.start = startIndex;
  renderedWindow.end = endIndex;
  renderedWindow.virtualized = true;

  // Первая отрисовка после инвалидации (или смена вёрстки) — уточняем высоты
  // по факту и, если они разошлись, пересчитываем смещения и рисуем окно
  // заново. При обычном скролле (measured=true) блок пропускается целиком,
  // т.е. getBoundingClientRect не вызывается на каждом кадре (см. FP1). Флаг
  // ставим только когда в DOM уже есть реальная строка — иначе меряли бы по
  // пустому/скелетному списку и «залипли» бы на неверной высоте.
  if (!washListMetricsResyncing && !washListMetricsMeasured) {
    const hasMeasurableRow = !!washList.querySelector(
      ".wash-row:not(.wash-row--skeleton)"
    );
    if (hasMeasurableRow) {
      const changed = syncWashListMetricsFromDom();
      washListMetricsMeasured = true;
      if (changed) {
        washListMetricsResyncing = true;
        try {
          buildWashDisplayItems();
          renderVirtualizedWashList();
        } finally {
          washListMetricsResyncing = false;
        }
      }
    }
  }
}

export function scheduleVirtualizedWashList() {
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

export function renderWashList(options = {}) {
  const { resetScroll = true } = options;
  state.filteredRows = getFilteredRows();
  updateWashFilterCount(state.filteredRows.length);
  buildWashDisplayItems();
  renderVirtualizedWashList({ resetScroll });
}

// Плоский список элементов для виртуализации: строки + (при сортировке по дате)
// заголовки-разделители по дням. Считаем префикс-суммы смещений для смешанных высот.
function buildWashDisplayItems() {
  const rows = state.filteredRows;
  const groupByDay = sortOrder && (sortOrder.value === "date_desc" || sortOrder.value === "date_asc");
  const items = [];
  let lastDay = null;
  for (const row of rows) {
    if (groupByDay) {
      const day = row.start_day || "";
      if (day !== lastDay) {
        lastDay = day;
        items.push({
          type: "header",
          label: formatDayHeader(day),
          height: washListMetrics.headerHeight,
        });
      }
    }
    items.push({ type: "row", row, height: washListMetrics.rowHeight });
  }

  const offsets = new Array(items.length + 1);
  offsets[0] = 0;
  // Индексы строк-моек (без заголовков дней) — для стрелочной навиг: раньше
  // moveWashSelection пересканировал весь displayItems на КАЖДОЕ нажатие
  // стрелки; тут собираем один раз в уже существующем проходе по offsets.
  const rowIndices = [];
  for (let i = 0; i < items.length; i += 1) {
    offsets[i + 1] = offsets[i] + items[i].height;
    if (items[i].type === "row") {
      rowIndices.push(i);
    }
  }
  state.displayItems = items;
  state.displayOffsets = offsets;
  state.displayRowIndices = rowIndices;
  state.displayTotalHeight = offsets[items.length];
  // Версия набора элементов: по ней кэш отрисованного окна понимает, что
  // список изменился и перерисовка нужна даже при тех же границах.
  state.displayVersion += 1;
}

// Первый элемент, нижняя граница которого больше value (бинарный поиск по offsets).
function findFirstVisibleItem(offsets, value) {
  let lo = 0;
  let hi = offsets.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (offsets[mid + 1] <= value) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }
  return lo;
}

// Отмена отложенного рендера поиска (таймер живёт здесь: импортированную
// переменную снаружи модуля не присвоить).
export function cancelScheduledSearchRender() {
  if (searchRenderTimer) {
    window.clearTimeout(searchRenderTimer);
    searchRenderTimer = 0;
  }
}

export function scheduleSearchRender() {
  if (searchRenderTimer) {
    window.clearTimeout(searchRenderTimer);
  }

  searchRenderTimer = window.setTimeout(() => {
    searchRenderTimer = 0;
    renderWashList({ resetScroll: true });
  }, SEARCH_INPUT_DEBOUNCE_MS);
}

export function applyObjectNameToWashData(channel, objectId, objectName) {
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
