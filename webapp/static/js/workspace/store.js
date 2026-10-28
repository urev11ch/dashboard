// Данные журнала моек без DOM: состояние страницы, подписи потоков, индексы
// строк по объектам. Импортируется всеми модулями рабочей области; без DOM —
// поэтому покрыто тестами Vitest (tests/js/store.test.js).

export const DEFAULT_PERIOD_PRESET = "7d";
// Пресет → сколько календарных суток показываем, считая сегодняшние.
export const PERIOD_PRESET_DAYS = { "7d": 7, "30d": 30 };
export const state = {
  washRows: [],
  channelLabels: new Map(),
  washRowIndexesByObjectKey: new Map(),
  objectRows: [],
  // Редактор названий: активная вкладка и строки вкладки «Программы».
  editorTab: "objects",
  programRows: [],
  filteredRows: [],
  displayItems: [],
  displayOffsets: [0],
  displayRowIndices: [],
  displayTotalHeight: 0,
  displayVersion: 0,
  dateBounds: null,
  activePeriodPreset: DEFAULT_PERIOD_PRESET,
  // Смещение таймзоны сервера (минуты к UTC). Приходит в /api/workspace-data;
  // если поля нет — считаем дни в зоне браузера (прежнее поведение).
  serverTzOffsetMin: null,
  // Обновление: последний ответ /api/update-check и состояние скачивания.
  updateInfo: null,
  updateJob: null,
  // Защёлка «скачивание/установка уже идёт»: disabled у кнопки живёт лишь до
  // ближайшей перерисовки панели, а она перерисовывается на каждом опросе.
  updateBusy: false,
};

export function buildSearchBlob(row) {
  return [
    row.object,
    row.program,
    row.date_time,
    row.source_name,
    row.status,
    row.channel_label || `Канал ${row.channel}`,
  ]
    .join(" ")
    .toLowerCase();
}

export function normalizeObjectName(value) {
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

export function replaceObjectRows(rows) {
  state.objectRows = sortObjectRows((Array.isArray(rows) ? rows : []).map((row) => ({ ...row })));
  rememberChannelLabels(state.objectRows);
}

// Подписи потоков: имя лога на панели («CIP», «Мойка ЦЕХ2») или «Канал N» для
// файлов вида Canal_1_*.db. Держим отдельной картой, чтобы не сканировать
// тысячи строк журнала на каждую отрисовку.
function rememberChannelLabels(rows) {
  (Array.isArray(rows) ? rows : []).forEach((row) => {
    const channel = Number(row && row.channel);
    const label = String((row && row.channel_label) || "").trim();
    if (Number.isInteger(channel) && channel >= 1 && label) {
      state.channelLabels.set(channel, label);
    }
  });
}

export function channelLabelFor(channel) {
  const value = Number(channel);
  return state.channelLabels.get(value) || `Канал ${channel}`;
}

// Список потоков считаем по конкретному набору строк, а не по карте подписей:
// карта живёт между сменами источника и может помнить исчезнувшие логи.
export function channelsFromRows(rows) {
  const channels = [
    ...new Set(
      (Array.isArray(rows) ? rows : [])
        .map((row) => Number(row && row.channel))
        .filter((channel) => Number.isInteger(channel) && channel >= 1)
    ),
  ];
  return channels.sort((a, b) =>
    channelLabelFor(a).localeCompare(channelLabelFor(b), "ru", { numeric: true })
  );
}

export function buildWashObjectKey(channel, objectId) {
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

export function replaceWashRows(rows) {
  state.washRows = (Array.isArray(rows) ? rows : []).map((row) => {
    const nextRow = { ...row };
    if (!nextRow.search_blob) {
      nextRow.search_blob = buildSearchBlob(nextRow);
    }
    return nextRow;
  });
  state.dateBounds = null;
  rememberChannelLabels(state.washRows);
  rebuildWashRowIndexes();
}
