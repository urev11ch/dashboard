// Раздел «График» в настройках: стили кривых (цвет, толщина, тип линии).

import { apiJson } from "../core/api.js";

// Стили линий, экранирование и проверка цвета — из wash-chart.js; список стилей
// должен совпадать с CHART_LINE_STYLE_IDS на сервере.
const {
  LINE_STYLE_OPTIONS: CHART_LINE_STYLE_OPTIONS,
  escapeHtml,
  isValidHexColor: isValidHexColorLike,
} = window.WashChart;

export async function fetchChartStyles() {
  const payload = await apiJson("/api/chart-styles");
  return {
    series: payload && typeof payload.series === "object" ? payload.series : {},
    defaults: Array.isArray(payload?.defaults) ? payload.defaults : [],
  };
}

export async function saveChartStyles(series) {
  const payload = await apiJson("/api/chart-styles", { method: "POST", body: { series } });
  return payload && typeof payload.series === "object" ? payload.series : series;
}

export function renderChartStyleControls(defaults, series) {
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
