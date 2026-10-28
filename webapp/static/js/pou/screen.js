// Экран ПОУ: тренды всех тегов архива за выбранный период (сутки, 3 дня,
// неделя, весь период или конкретный день). Метки архива — часы панели,
// записанные как UTC, поэтому границы дня считаются в UTC (как в журнале моек).

import { apiJson } from "../core/api.js";
import { formatEpochSeconds, getServerDayStartTs } from "../core/format.js";
import { showToast } from "../core/toast.js";

const DAY_SECONDS = 86400;

export function initPouScreen() {
  const root = document.querySelector("[data-pou-screen]");
  if (!root) {
    return;
  }
  const host = root.querySelector("[data-chart-host]");
  const empty = root.querySelector("[data-chart-empty]");
  const stats = root.querySelector("[data-pou-stats]");
  const rangeLabel = root.querySelector("[data-pou-range]");
  const dayInput = root.querySelector("[data-pou-day]");
  const presetButtons = Array.from(root.querySelectorAll("[data-pou-period]"));

  let bounds = null; // {start, end} — секунды архива
  let requestId = 0;

  const setEmpty = (message) => {
    empty.textContent = message;
    empty.hidden = !message;
    host.hidden = Boolean(message);
  };

  const markPreset = (value) => {
    presetButtons.forEach((button) => {
      button.classList.toggle("is-active", button.dataset.pouPeriod === value);
    });
  };

  const loadRange = async (start, end) => {
    const current = ++requestId;
    rangeLabel.textContent = `${formatEpochSeconds(start)} — ${formatEpochSeconds(end)}`;
    setEmpty("Загружаю данные.");
    try {
      const payload = await apiJson(
        `/api/pou/trend?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,
        { timeout: 60000, errorMessage: "Не удалось загрузить тренд." }
      );
      if (current !== requestId) {
        return; // пользователь уже выбрал другой период
      }
      if (!payload?.has_data) {
        setEmpty("Нет данных за выбранный период.");
        return;
      }
      setEmpty("");
      host.innerHTML = "";
      await (window.WashChart?.hydrate ? window.WashChart.hydrate() : Promise.resolve());
      if (current !== requestId) {
        return;
      }
      if (!window.WashChart?.mount(host, payload)) {
        setEmpty("Не удалось построить график.");
      }
    } catch (error) {
      if (current === requestId) {
        setEmpty("Не удалось загрузить данные.");
        showToast(error instanceof Error ? error.message : "Не удалось загрузить тренд.", "error");
      }
    }
  };

  const applyPreset = (value) => {
    if (!bounds) {
      return;
    }
    markPreset(value);
    dayInput.value = "";
    if (value === "all") {
      void loadRange(bounds.start, bounds.end);
      return;
    }
    const days = Number(value) || 1;
    // Период отсчитывается от последней записи архива, а не от «сейчас»:
    // архив мог не обновляться, и «сутки до сейчас» оказались бы пустыми.
    void loadRange(Math.max(bounds.start, bounds.end - days * DAY_SECONDS), bounds.end);
  };

  presetButtons.forEach((button) => {
    button.addEventListener("click", () => applyPreset(button.dataset.pouPeriod));
  });

  dayInput.addEventListener("change", () => {
    const start = getServerDayStartTs(dayInput.value);
    if (start === null) {
      return;
    }
    markPreset("");
    void loadRange(start, start + DAY_SECONDS - 1);
  });

  const pill = (text) => {
    const element = document.createElement("div");
    element.className = "stat-pill";
    element.textContent = text;
    return element;
  };

  void (async () => {
    try {
      const summary = await apiJson("/api/pou/summary", { errorMessage: "Не удалось открыть данные ПОУ." });
      const pou = summary?.pou;
      if (!pou || pou.start === null || pou.end === null) {
        setEmpty("В архиве нет записей.");
        return;
      }
      bounds = { start: pou.start, end: pou.end };
      stats.replaceChildren(
        pill(`Баз: ${pou.db_count}`),
        pill(`Тегов: ${pou.columns.length}`),
        pill(`Записей: ${pou.row_count}`)
      );
      // Календарь ограничен днями, где есть данные (UTC = часы панели).
      dayInput.min = new Date(pou.start * 1000).toISOString().slice(0, 10);
      dayInput.max = new Date(pou.end * 1000).toISOString().slice(0, 10);
      applyPreset("1");
    } catch (error) {
      setEmpty("Не удалось открыть данные ПОУ.");
      showToast(error instanceof Error ? error.message : "Не удалось открыть данные ПОУ.", "error");
    }
  })();
}
