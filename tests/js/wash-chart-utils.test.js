// Unit-тесты чистых функций графика (webapp/static/wash-chart.js).
// Модуль экспортирует их через module.exports в конце IIFE (только для Node).
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { describe, it, expect } from "vitest";

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));
const chart = require(path.join(here, "../../webapp/static/wash-chart.js"));

describe("clamp", () => {
  it("зажимает значение в границы", () => {
    expect(chart.clamp(5, 0, 10)).toBe(5);
    expect(chart.clamp(-3, 0, 10)).toBe(0);
    expect(chart.clamp(42, 0, 10)).toBe(10);
  });
});

describe("isValidHexColor", () => {
  it("принимает только 6-значный #rrggbb, отклоняет остальное", () => {
    expect(chart.isValidHexColor("#00AAFF")).toBe(true);
    expect(chart.isValidHexColor("#00aaff")).toBe(true);
    expect(chart.isValidHexColor("#0af")).toBe(false); // 3-значный не поддерживается
    expect(chart.isValidHexColor("red")).toBe(false);
    expect(chart.isValidHexColor("#12")).toBe(false);
    expect(chart.isValidHexColor("")).toBe(false);
  });
});

describe("escapeHtml", () => {
  it("экранирует спецсимволы", () => {
    expect(chart.escapeHtml('<a href="x">&</a>')).toBe(
      "&lt;a href=&quot;x&quot;&gt;&amp;&lt;/a&gt;"
    );
  });
});

describe("padRange", () => {
  it("расширяет диапазон и обрабатывает min===max", () => {
    const [lo, hi] = chart.padRange(0, 10);
    expect(lo).toBeLessThanOrEqual(0);
    expect(hi).toBeGreaterThanOrEqual(10);
    const [lo2, hi2] = chart.padRange(5, 5);
    expect(lo2).toBeLessThan(hi2); // не нулевой диапазон
  });
});

describe("getNiceStep", () => {
  it("возвращает «красивый» шаг", () => {
    expect(chart.getNiceStep(1)).toBeGreaterThan(0);
    expect(chart.getNiceStep(0)).toBeGreaterThan(0);
    expect(chart.getNiceStep(7)).toBeGreaterThanOrEqual(5);
  });
});

describe("buildAxisTicks", () => {
  it("тики монотонны и покрывают диапазон", () => {
    const ticks = chart.buildAxisTicks(0, 100);
    expect(ticks.length).toBeGreaterThan(1);
    for (let i = 1; i < ticks.length; i += 1) {
      expect(ticks[i]).toBeGreaterThan(ticks[i - 1]);
    }
  });
  it("не падает на нулевом диапазоне", () => {
    expect(() => chart.buildAxisTicks(5, 5)).not.toThrow();
  });
});

describe("findNearestPointIndex", () => {
  const points = [
    [0, 1],
    [10, 2],
    [20, 3],
    [30, 4],
  ];
  it("находит ближайшую по времени точку", () => {
    expect(chart.findNearestPointIndex(points, 0)).toBe(0);
    expect(chart.findNearestPointIndex(points, 9)).toBe(1);
    expect(chart.findNearestPointIndex(points, 26)).toBe(3);
    expect(chart.findNearestPointIndex(points, 1000)).toBe(3);
    expect(chart.findNearestPointIndex(points, -1000)).toBe(0);
  });
});

describe("getLineStyleOption", () => {
  it("возвращает известный стиль или solid по умолчанию", () => {
    expect(chart.getLineStyleOption("dashed").id).toBe("dashed");
    expect(chart.getLineStyleOption("нет-такого").id).toBe("solid");
  });
});

describe("formatValue", () => {
  it("форматирует число с единицей и не падает на нечисле", () => {
    expect(typeof chart.formatValue(12.34, "%")).toBe("string");
    expect(() => chart.formatValue(NaN, "%")).not.toThrow();
  });
});
