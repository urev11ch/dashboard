// Просмотр содержимого `.db`: сырые таблицы панели — что именно записано в архиве.
// Корневой элемент окна создаёт main.js (порядок окон в DOM задаёт наложение)
// и передаёт в initDbBrowser вместе с beforeOpen (закрыть окно мойки).

import { apiJson, fetchWithTimeout } from "./core/api.js";
import { formatBytes, formatEpochSeconds } from "./core/format.js";
import { syncOverlayState } from "./core/overlays.js";
import { showToast } from "./core/toast.js";

const { escapeHtml } = window.WashChart;

let dbBrowserRoot = null;
let beforeOpen = () => {};

export function initDbBrowser(root, hooks = {}) {
  dbBrowserRoot = root;
  beforeOpen = hooks.beforeOpen || beforeOpen;
}

// Сырые таблицы `.db` панели: что именно записано в архиве. Данные тянем
// страницами — в `data` бывают десятки тысяч строк.
const dbBrowser = {
  files: [],
  path: "",
  tables: [],
  table: "",
  offset: 0,
  limit: 250,
  loading: false,
};
// Размер страницы: потолок на сервере — ROWS_PAGE_MAX (500).
const DB_PAGE_SIZES = [100, 250, 500];

function isTimestampColumn(name) {
  return /timestamp|time@/i.test(String(name || ""));
}

function formatDbCell(value, column) {
  if (value === null || value === undefined) {
    return '<span class="db-browser-null">NULL</span>';
  }
  if (isTimestampColumn(column) && typeof value === "number" && value > 10 ** 8 && value < 10 ** 11) {
    // Метку времени панели показываем датой, сырое значение — в подсказке.
    // Формат и зона — те же, что в журнале (зона сервера), иначе одна и та же
    // мойка выглядела бы по-разному в списке и в просмотрщике базы.
    const text = formatEpochSeconds(value);
    if (text !== "—") {
      return `<span title="${escapeHtml(value)}">${escapeHtml(text)}</span>`;
    }
  }
  return escapeHtml(value);
}

function loadDbBrowserTables(path) {
  return apiJson(`/api/db-browser/tables?path=${encodeURIComponent(path)}`);
}

function loadDbBrowserRows(path, table, offset, limit) {
  const query = new URLSearchParams({ path, table, offset: String(offset), limit: String(limit) });
  return apiJson(`/api/db-browser/rows?${query}`);
}

function renderDbBrowserFileOptions() {
  if (!dbBrowser.files.length) {
    return '<option value="">Баз не найдено</option>';
  }
  return dbBrowser.files
    .map((file) => {
      const size = file.size === null || file.size === undefined ? "" : ` · ${formatBytes(file.size)}`;
      const stream = file.stream ? ` · ${file.stream}` : "";
      return `<option value="${escapeHtml(file.path)}"${file.path === dbBrowser.path ? " selected" : ""}>${escapeHtml(
        file.name
      )}${escapeHtml(stream)}${escapeHtml(size)}</option>`;
    })
    .join("");
}

function renderDbBrowserTableChips() {
  if (!dbBrowser.tables.length) {
    return '<span class="db-browser-hint">Таблиц нет</span>';
  }
  return dbBrowser.tables
    .map(
      (table) => `
        <button
          type="button"
          class="toolbar-sort-option${table.name === dbBrowser.table ? " is-active" : ""}"
          data-db-table="${escapeHtml(table.name)}"
        >${escapeHtml(table.name)} <span class="db-browser-count">${
          table.row_count < 0 ? "?" : table.row_count
        }</span></button>
      `
    )
    .join("");
}

function renderDbBrowserGrid(payload) {
  if (!payload || !payload.columns.length) {
    return '<div class="technical-empty">Выберите таблицу.</div>';
  }
  if (!payload.rows.length) {
    return '<div class="technical-empty">В таблице нет строк.</div>';
  }
  const labels = payload.column_labels || {};
  const head = payload.columns
    .map((column) => {
      const label = labels[column];
      // У панели человекочитаемые названия тегов лежат в таблице data_format —
      // сырое `data_format_3` само по себе ни о чём не говорит.
      return label
        ? `<th>${escapeHtml(label)}<span class="db-browser-colname">${escapeHtml(column)}</span></th>`
        : `<th>${escapeHtml(column)}</th>`;
    })
    .join("");
  const body = payload.rows
    .map(
      (row, index) =>
        `<tr><td class="db-browser-rownum">${payload.offset + index + 1}</td>${row
          .map((value, cell) => `<td>${formatDbCell(value, payload.columns[cell])}</td>`)
          .join("")}</tr>`
    )
    .join("");
  return `
    <table class="db-browser-table">
      <thead><tr><th class="db-browser-rownum">#</th>${head}</tr></thead>
      <tbody>${body}</tbody>
    </table>
  `;
}

function renderDbBrowserFooter(payload) {
  const total = payload ? payload.total : 0;
  const from = total ? dbBrowser.offset + 1 : 0;
  const to = payload ? Math.min(total, dbBrowser.offset + payload.rows.length) : 0;
  const atStart = dbBrowser.offset <= 0;
  const atEnd = !payload || dbBrowser.offset + dbBrowser.limit >= total;
  const sizes = DB_PAGE_SIZES.map(
    (size) => `<option value="${size}"${size === dbBrowser.limit ? " selected" : ""}>${size}</option>`
  ).join("");
  return `
    <label class="db-browser-page-size">
      <span>Строк</span>
      <select data-db-page-size>${sizes}</select>
    </label>
    <button type="button" class="ghost" data-db-page="first" ${atStart ? "disabled" : ""}>В начало</button>
    <button type="button" class="ghost" data-db-page="prev" ${atStart ? "disabled" : ""}>Назад</button>
    <span class="db-browser-position">${from}–${to} из ${total}</span>
    <button type="button" class="ghost" data-db-page="next" ${atEnd ? "disabled" : ""}>Вперёд</button>
    <button type="button" class="ghost" data-db-page="last" ${atEnd ? "disabled" : ""}>В конец</button>
  `;
}

async function refreshDbBrowserRows() {
  const grid = dbBrowserRoot.querySelector("[data-db-grid]");
  const footer = dbBrowserRoot.querySelector("[data-db-footer]");
  if (!grid || !dbBrowser.path || !dbBrowser.table) {
    if (grid) {
      grid.innerHTML = '<div class="technical-empty">Выберите таблицу.</div>';
    }
    if (footer) {
      footer.innerHTML = "";
    }
    return;
  }

  dbBrowser.loading = true;
  grid.innerHTML = '<div class="technical-empty">Загрузка…</div>';
  try {
    const payload = await loadDbBrowserRows(
      dbBrowser.path,
      dbBrowser.table,
      dbBrowser.offset,
      dbBrowser.limit
    );
    // Пока грузили, пользователь мог переключить таблицу — ответ устарел.
    if (payload.table !== dbBrowser.table) {
      return;
    }
    grid.innerHTML = renderDbBrowserGrid(payload);
    if (footer) {
      footer.innerHTML = renderDbBrowserFooter(payload);
    }
  } catch (error) {
    grid.innerHTML = '<div class="technical-empty">Не удалось прочитать таблицу.</div>';
    if (footer) {
      footer.innerHTML = "";
    }
  } finally {
    dbBrowser.loading = false;
  }
}

async function selectDbBrowserFile(path) {
  dbBrowser.path = path;
  dbBrowser.tables = [];
  dbBrowser.table = "";
  dbBrowser.offset = 0;

  const chips = dbBrowserRoot.querySelector("[data-db-tables]");
  if (chips) {
    chips.innerHTML = '<span class="db-browser-hint">Загрузка…</span>';
  }

  try {
    const payload = await loadDbBrowserTables(path);
    dbBrowser.tables = payload.tables || [];
    // Стартуем с самой содержательной таблицы — обычно это `data`.
    const preferred =
      dbBrowser.tables.find((table) => table.name === "data") ||
      dbBrowser.tables.slice().sort((a, b) => b.row_count - a.row_count)[0];
    dbBrowser.table = preferred ? preferred.name : "";
  } catch (error) {
    dbBrowser.tables = [];
    dbBrowser.table = "";
    showToast("Не удалось прочитать список таблиц.", "error");
  }

  if (chips) {
    chips.innerHTML = renderDbBrowserTableChips();
  }
  await refreshDbBrowserRows();
}

export function closeDbBrowser() {
  dbBrowserRoot.hidden = true;
  dbBrowserRoot.innerHTML = "";
  syncOverlayState();
}

export async function openDbBrowser() {
  beforeOpen();
  dbBrowserRoot.hidden = false;
  syncOverlayState();
  dbBrowserRoot.innerHTML = `
    <div class="object-editor-backdrop" data-close-db-browser></div>
    <section class="object-editor-panel db-browser-panel" role="dialog" aria-modal="true" aria-label="Содержимое базы данных">
      <header class="object-editor-header db-browser-header">
        <h2>Содержимое базы</h2>
        <label class="db-browser-file">
          <select data-db-file aria-label="База данных"></select>
        </label>
        <div class="toolbar-sort-grid db-browser-tables" data-db-tables></div>
        <button type="button" class="chart-modal-icon-button chart-modal-icon-button--danger" data-close-db-browser aria-label="Закрыть просмотр базы" title="Закрыть">
          <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
            <path d="M5 5L15 15"></path>
            <path d="M15 5L5 15"></path>
          </svg>
        </button>
      </header>
      <div class="db-browser-grid" data-db-grid>
        <div class="technical-empty">Загрузка…</div>
      </div>
      <footer class="object-editor-footer db-browser-footer" data-db-footer></footer>
    </section>
  `;

  dbBrowserRoot.querySelectorAll("[data-close-db-browser]").forEach((element) => {
    element.addEventListener("click", closeDbBrowser);
  });

  const select = dbBrowserRoot.querySelector("[data-db-file]");
  if (select) {
    select.addEventListener("change", (event) => {
      selectDbBrowserFile(event.currentTarget.value);
    });
  }

  const chips = dbBrowserRoot.querySelector("[data-db-tables]");
  if (chips) {
    chips.addEventListener("click", (event) => {
      const button = event.target.closest("[data-db-table]");
      if (!button || dbBrowser.loading) {
        return;
      }
      dbBrowser.table = button.dataset.dbTable;
      dbBrowser.offset = 0;
      chips.innerHTML = renderDbBrowserTableChips();
      refreshDbBrowserRows();
    });
  }

  const footer = dbBrowserRoot.querySelector("[data-db-footer]");
  if (footer) {
    footer.addEventListener("change", (event) => {
      const select = event.target.closest("[data-db-page-size]");
      if (!select) {
        return;
      }
      // Держим в поле зрения первую строку текущей страницы, чтобы смена
      // размера не выбрасывала пользователя в другое место таблицы.
      const nextLimit = Number(select.value) || dbBrowser.limit;
      dbBrowser.offset = Math.floor(dbBrowser.offset / nextLimit) * nextLimit;
      dbBrowser.limit = nextLimit;
      refreshDbBrowserRows();
    });
    footer.addEventListener("click", (event) => {
      const button = event.target.closest("[data-db-page]");
      if (!button || dbBrowser.loading) {
        return;
      }
      const table = dbBrowser.tables.find((item) => item.name === dbBrowser.table);
      const total = table ? Math.max(0, table.row_count) : 0;
      const step = dbBrowser.limit;
      const moves = {
        first: 0,
        prev: Math.max(0, dbBrowser.offset - step),
        next: dbBrowser.offset + step,
        last: Math.max(0, Math.floor(Math.max(0, total - 1) / step) * step),
      };
      dbBrowser.offset = moves[button.dataset.dbPage] ?? dbBrowser.offset;
      refreshDbBrowserRows();
    });
  }

  try {
    const response = await fetchWithTimeout("/api/db-browser/files", {
      headers: { Accept: "application/json" },
    });
    dbBrowser.files = response.ok ? (await response.json()).files || [] : [];
  } catch (error) {
    dbBrowser.files = [];
  }

  if (select) {
    select.innerHTML = renderDbBrowserFileOptions();
  }
  if (dbBrowser.files.length) {
    const known = dbBrowser.files.some((file) => file.path === dbBrowser.path);
    await selectDbBrowserFile(known ? dbBrowser.path : dbBrowser.files[0].path);
  } else {
    const grid = dbBrowserRoot.querySelector("[data-db-grid]");
    if (grid) {
      grid.innerHTML = '<div class="technical-empty">Сначала откройте источник данных.</div>';
    }
  }
}
