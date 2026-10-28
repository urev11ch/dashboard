// Unit-тесты данных журнала моек (webapp/static/js/workspace/store.js).
// Модуль без DOM. state — общий синглтон модуля, поэтому перед каждым тестом
// сбрасываем строки и подписи потоков.
import { beforeEach, describe, it, expect } from "vitest";
import {
  DEFAULT_PERIOD_PRESET,
  PERIOD_PRESET_DAYS,
  buildSearchBlob,
  buildWashObjectKey,
  channelLabelFor,
  channelsFromRows,
  normalizeObjectName,
  replaceObjectRows,
  replaceWashRows,
  state,
} from "../../webapp/static/js/workspace/store.js";

beforeEach(() => {
  state.channelLabels.clear();
  replaceObjectRows([]);
  replaceWashRows([]);
});

describe("пресеты периода", () => {
  it("по умолчанию — 7 дней, пресет есть в таблице", () => {
    expect(DEFAULT_PERIOD_PRESET).toBe("7d");
    expect(PERIOD_PRESET_DAYS[DEFAULT_PERIOD_PRESET]).toBe(7);
    expect(state.activePeriodPreset).toBe(DEFAULT_PERIOD_PRESET);
  });
});

describe("normalizeObjectName", () => {
  it("схлопывает пробелы и обрезает края", () => {
    expect(normalizeObjectName("  Танк   №1 \n")).toBe("Танк №1");
  });

  it("пустое значение — пустая строка", () => {
    expect(normalizeObjectName(null)).toBe("");
    expect(normalizeObjectName(undefined)).toBe("");
  });
});

describe("buildWashObjectKey", () => {
  it("канал:объект числами, мусор — нулём", () => {
    expect(buildWashObjectKey("2", "07")).toBe("2:7");
    expect(buildWashObjectKey(undefined, "x")).toBe("0:0");
  });
});

describe("buildSearchBlob", () => {
  it("склеивает поля поиска в нижнем регистре", () => {
    const blob = buildSearchBlob({
      object: "Танк",
      program: "Щёлочь",
      date_time: "2026-07-14 09:00:00",
      source_name: "Canal_1.db",
      status: "Завершено",
      channel: 1,
    });
    expect(blob).toBe("танк щёлочь 2026-07-14 09:00:00 canal_1.db завершено канал 1");
  });

  it("подпись потока берёт channel_label, если он есть", () => {
    expect(buildSearchBlob({ channel: 3, channel_label: "CIP" })).toContain("cip");
  });
});

describe("подписи потоков", () => {
  it("без подписи — «Канал N»", () => {
    expect(channelLabelFor(4)).toBe("Канал 4");
  });

  it("запоминает channel_label из строк объектов", () => {
    replaceObjectRows([{ channel: 2, object_id: 1, channel_label: "Мойка ЦЕХ2" }]);
    expect(channelLabelFor(2)).toBe("Мойка ЦЕХ2");
    expect(channelLabelFor("2")).toBe("Мойка ЦЕХ2");
  });

  it("игнорирует пустые подписи и неверные номера каналов", () => {
    replaceObjectRows([
      { channel: 0, object_id: 1, channel_label: "Ноль" },
      { channel: 5, object_id: 1, channel_label: "   " },
    ]);
    expect(channelLabelFor(0)).toBe("Канал 0");
    expect(channelLabelFor(5)).toBe("Канал 5");
  });
});

describe("channelsFromRows", () => {
  it("уникальные каналы, порядок — по подписи с числовым сравнением", () => {
    const rows = [{ channel: 10 }, { channel: 2 }, { channel: 2 }, { channel: 1 }, { channel: "x" }];
    expect(channelsFromRows(rows)).toEqual([1, 2, 10]);
  });

  it("сортирует по подписи, а не по номеру", () => {
    replaceObjectRows([
      { channel: 1, object_id: 1, channel_label: "Я-линия" },
      { channel: 2, object_id: 1, channel_label: "А-линия" },
    ]);
    expect(channelsFromRows([{ channel: 1 }, { channel: 2 }])).toEqual([2, 1]);
  });

  it("не массив — пустой список", () => {
    expect(channelsFromRows(null)).toEqual([]);
  });
});

describe("replaceObjectRows", () => {
  it("копирует строки и сортирует по каналу, затем по объекту", () => {
    const source = [
      { channel: 2, object_id: 1 },
      { channel: 1, object_id: 3 },
      { channel: 1, object_id: 2 },
    ];
    replaceObjectRows(source);
    expect(state.objectRows.map((r) => `${r.channel}:${r.object_id}`)).toEqual(["1:2", "1:3", "2:1"]);
    state.objectRows[0].object_name = "изменено";
    expect(source[2].object_name).toBeUndefined(); // исходные строки не мутируются
  });
});

describe("replaceWashRows", () => {
  it("дополняет search_blob, если его нет, и не трогает готовый", () => {
    replaceWashRows([
      { channel: 1, object_id: 1, object: "Танк" },
      { channel: 1, object_id: 2, search_blob: "готовый" },
    ]);
    expect(state.washRows[0].search_blob).toContain("танк");
    expect(state.washRows[1].search_blob).toBe("готовый");
  });

  it("строит индекс строк по объекту и сбрасывает границы дат", () => {
    state.dateBounds = { min: "2026-01-01", max: "2026-01-02" };
    replaceWashRows([
      { channel: 1, object_id: 1 },
      { channel: 2, object_id: 1 },
      { channel: 1, object_id: 1 },
    ]);
    expect(state.washRowIndexesByObjectKey.get("1:1")).toEqual([0, 2]);
    expect(state.washRowIndexesByObjectKey.get("2:1")).toEqual([1]);
    expect(state.dateBounds).toBeNull();
  });

  it("запоминает подписи потоков из строк журнала", () => {
    replaceWashRows([{ channel: 7, object_id: 1, channel_label: "CIP-7" }]);
    expect(channelLabelFor(7)).toBe("CIP-7");
  });
});
