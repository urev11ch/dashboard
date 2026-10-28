// Форматирование значений для интерфейса: даты, метки архива, размеры, концентрации.
// Чистые функции без DOM и общего состояния — покрыты тестами Vitest
// (tests/js/format.test.js).

// Норматив концентрации для поля ввода: число или пусто (не задан).
export function formatNormValue(value) {
  return value === null || value === undefined || value === "" ? "" : String(value);
}

export function badgeClass(status, kind) {
  // Цвет по категории результата (независимо от текста подписи). Если категория
  // не передана — резерв по тексту для обратной совместимости.
  const resolved = kind || (String(status || "").startsWith("Завершено") ? "completed" : "check");
  return resolved === "completed" ? "badge ok" : "badge warn";
}

// Единый формат даты и времени во всём приложении: ДД.ММ.ГГГГ ЧЧ:ММ:СС.
// Раньше карточка мойки и печатный отчёт писали «14.07.2026. 12.00.00»
// (точки вместо двоеточий), список — «14.07.2026 12:00:00», а диагностика
// отдавала сырое «2026-07-14 12:00:00» с бэкенда: три вида одной метки.
//
// Возвращает сырую строку: экранирование — только в месте вставки в разметку,
// иначе получалось двойное экранирование (&amp;quot; в тултипе).
export function formatDateTime(value) {
  const match = String(value ?? "").match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})$/);
  if (!match) {
    return String(value ?? "—");
  }

  const [, year, month, day, hours, minutes, seconds] = match;
  return `${day}.${month}.${year} ${hours}:${minutes}:${seconds}`;
}

// Метка архива (epoch-секунды) в том же формате — как format_ts на бэкенде.
// Панель пишет в архив показания своих часов как UTC, поэтому компоненты
// читаем как UTC без сдвига на зону компьютера.
export function formatEpochSeconds(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value)) {
    return "—";
  }

  const date = new Date(value * 1000);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }

  const pad = (part) => String(part).padStart(2, "0");
  return `${pad(date.getUTCDate())}.${pad(date.getUTCMonth() + 1)}.${date.getUTCFullYear()} ${pad(
    date.getUTCHours()
  )}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())}`;
}

export function getLocalDateKey(value = new Date()) {
  const date = value instanceof Date ? value : new Date(value);
  const year = String(date.getFullYear());
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

// Начало суток YYYY-MM-DD в шкале меток архива (epoch, секунды): метки — это
// часы панели, записанные как UTC, поэтому полночь панели — это Date.UTC.
export function getServerDayStartTs(dayKey) {
  const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(dayKey || ""));
  if (!parts) {
    return null;
  }

  const [, year, month, day] = parts;
  return Date.UTC(Number(year), Number(month) - 1, Number(day)) / 1000;
}

export function formatDayHeader(day) {
  const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(day || ""));
  if (!parts) {
    return "Без даты";
  }
  const date = new Date(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]));
  return date.toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
}

// Число с одним знаком после запятой (концентрация/уставка), либо «—».
export function formatConcentration(value) {
  return value === null || value === undefined || !Number.isFinite(Number(value))
    ? "—"
    : `${Number(value).toFixed(1)} %`;
}

export function formatBytes(bytes) {
  const value = Number(bytes) || 0;
  if (value < 1024) {
    return `${value} Б`;
  }
  const units = ["КБ", "МБ", "ГБ", "ТБ"];
  let size = value / 1024;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[unitIndex]}`;
}
