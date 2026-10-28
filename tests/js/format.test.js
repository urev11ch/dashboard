// Unit-тесты форматирования интерфейса (webapp/static/js/core/format.js).
// Модуль — обычный ES-модуль без DOM, импортируется напрямую.
import { describe, it, expect } from "vitest";
import {
  badgeClass,
  formatBytes,
  formatConcentration,
  formatDateTime,
  formatDayHeader,
  formatEpochSeconds,
  formatNormValue,
  getLocalDateKey,
  getServerDayStartTs,
} from "../../webapp/static/js/core/format.js";

describe("formatNormValue", () => {
  it("пустое значение — пустая строка (норматив не задан)", () => {
    expect(formatNormValue(null)).toBe("");
    expect(formatNormValue(undefined)).toBe("");
    expect(formatNormValue("")).toBe("");
  });

  it("число и ноль — строкой", () => {
    expect(formatNormValue(1.5)).toBe("1.5");
    expect(formatNormValue(0)).toBe("0");
  });
});

describe("badgeClass", () => {
  it("цвет по категории, а не по тексту", () => {
    expect(badgeClass("Что угодно", "completed")).toBe("badge ok");
    expect(badgeClass("Завершено", "check")).toBe("badge warn");
  });

  it("без категории — резерв по тексту", () => {
    expect(badgeClass("Завершено успешно")).toBe("badge ok");
    expect(badgeClass("Прервано")).toBe("badge warn");
    expect(badgeClass(null)).toBe("badge warn");
  });
});

describe("formatDateTime", () => {
  it("серверную метку переводит в ДД.ММ.ГГГГ ЧЧ:ММ:СС", () => {
    expect(formatDateTime("2026-07-14 12:05:09")).toBe("14.07.2026 12:05:09");
    expect(formatDateTime("2026-07-14T12:05:09")).toBe("14.07.2026 12:05:09");
  });

  it("нераспознанное значение возвращает как есть, пустое — прочерком", () => {
    expect(formatDateTime("вчера")).toBe("вчера");
    expect(formatDateTime(null)).toBe("—");
    expect(formatDateTime(undefined)).toBe("—");
  });

  it("не экранирует: экранирование — в месте вставки в разметку", () => {
    expect(formatDateTime('<b>"x"</b>')).toBe('<b>"x"</b>');
  });
});

describe("formatEpochSeconds", () => {
  it("читает метку архива как UTC, без сдвига на зону компьютера", () => {
    // 2026-07-14 12:05:09 UTC
    expect(formatEpochSeconds(Date.UTC(2026, 6, 14, 12, 5, 9) / 1000)).toBe("14.07.2026 12:05:09");
    expect(formatEpochSeconds("0")).toBe("01.01.1970 00:00:00");
  });

  it("нечисловое значение — прочерк", () => {
    expect(formatEpochSeconds("abc")).toBe("—");
    expect(formatEpochSeconds(Infinity)).toBe("—");
    expect(formatEpochSeconds(1e20)).toBe("—"); // за пределами диапазона Date
  });
});

describe("getLocalDateKey", () => {
  it("ключ дня YYYY-MM-DD по местной дате", () => {
    expect(getLocalDateKey(new Date(2026, 0, 5, 23, 59))).toBe("2026-01-05");
    expect(getLocalDateKey(new Date(2026, 11, 31, 0, 0))).toBe("2026-12-31");
  });
});

describe("getServerDayStartTs", () => {
  it("полночь панели в шкале меток архива (UTC, секунды)", () => {
    expect(getServerDayStartTs("2026-07-14")).toBe(Date.UTC(2026, 6, 14) / 1000);
  });

  it("неверный ключ — null", () => {
    expect(getServerDayStartTs("14.07.2026")).toBeNull();
    expect(getServerDayStartTs("")).toBeNull();
    expect(getServerDayStartTs(null)).toBeNull();
  });
});

describe("formatDayHeader", () => {
  it("заголовок дня по-русски", () => {
    const header = formatDayHeader("2026-07-14");
    expect(header).toContain("14");
    expect(header).toContain("июля");
    expect(header).toContain("2026");
  });

  it("без даты", () => {
    expect(formatDayHeader("")).toBe("Без даты");
    expect(formatDayHeader("2026/07/14")).toBe("Без даты");
  });
});

describe("formatConcentration", () => {
  it("один знак после запятой и знак процента", () => {
    expect(formatConcentration(1.26)).toBe("1.3 %");
    expect(formatConcentration("2")).toBe("2.0 %");
    expect(formatConcentration(0)).toBe("0.0 %");
  });

  it("нет значения — прочерк", () => {
    expect(formatConcentration(null)).toBe("—");
    expect(formatConcentration(undefined)).toBe("—");
    expect(formatConcentration("n/a")).toBe("—");
  });
});

describe("formatBytes", () => {
  it("байты без дробной части", () => {
    expect(formatBytes(0)).toBe("0 Б");
    expect(formatBytes(1023)).toBe("1023 Б");
    expect(formatBytes(null)).toBe("0 Б");
  });

  it("меньше 10 единиц — один знак, больше — целое", () => {
    expect(formatBytes(1024)).toBe("1.0 КБ");
    expect(formatBytes(1536)).toBe("1.5 КБ");
    expect(formatBytes(20 * 1024)).toBe("20 КБ");
    expect(formatBytes(5 * 1024 ** 3)).toBe("5.0 ГБ");
  });

  it("выше ТБ не уходит", () => {
    expect(formatBytes(2048 * 1024 ** 4)).toBe("2048 ТБ");
  });
});
