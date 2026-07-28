import { describe, it, expect } from "vitest";
import { parseChannel, channelColor, channelPct, fmtChannel, fmtChannelShort } from "./channel";

// Разбор свободного текста — самое хрупкое место виджета: `Tariff.bandwidth`
// заполняется руками, и провайдеры пишут скорость и объём трафика одной строкой.

describe("parseChannel: форматы скорости", () => {
  const cases: Array<[string, number]> = [
    ["1 Гбит/с", 1000],
    ["1 гбит", 1000],
    ["1G", 1000],
    ["1 Gbps", 1000],
    ["1 Gbit/s", 1000],
    ["10G unmetered", 10000],
    ["100 Мбит/с", 100],
    ["100 Mbit", 100],
    ["500Mbps", 500],
    ["2.5 Гбит/с", 2500],
    ["2,5 Гбит", 2500],          // десятичная запятая
    ["10 Гбит/с, 100 ТБ", 10000], // скорость + лимит трафика в одной строке
    ["1 Гб/с", 1000],             // «Гб/с» с явным «в секунду» — это гигабиты, а не байты
  ];
  for (const [text, mbps] of cases) {
    it(`«${text}» → ${mbps} Мбит/с`, () => expect(parseChannel(text)).toBe(mbps));
  }
});

describe("parseChannel: объём трафика — это НЕ скорость", () => {
  for (const text of ["20 ТБ", "100 GB", "безлимит", "unmetered", "1 TB трафика", ""]) {
    it(`«${text}» → null`, () => expect(parseChannel(text)).toBeNull());
  }

  it("голое число не угадывается", () => {
    // «1000» одинаково похоже на мегабиты и на гигабайты — честнее показать текст.
    expect(parseChannel("1000")).toBeNull();
  });

  it("находит скорость, даже если объём стоит первым", () => {
    expect(parseChannel("20 ТБ, 1 Гбит/с")).toBe(1000);
  });
});

describe("channelColor: ступени", () => {
  it("разводит четыре ступени по границам", () => {
    const tiers = [channelColor(100), channelColor(151), channelColor(1001), channelColor(10001)];
    expect(new Set(tiers).size).toBe(4);
  });

  it("границы включаются в нижнюю ступень", () => {
    expect(channelColor(150)).toBe(channelColor(100));
    expect(channelColor(1000)).toBe(channelColor(151));
    expect(channelColor(10000)).toBe(channelColor(1001));
  });

  it("нераспознанный канал — нейтральный токен, а не цвет ступени", () => {
    expect(channelColor(null)).toBe("var(--t-low)");
    expect(channelColor(undefined)).toBe("var(--t-low)");
    expect(channelColor(NaN)).toBe("var(--t-low)");
  });
});

describe("channelPct: логарифмическая шкала", () => {
  it("растёт вместе с шириной канала", () => {
    const seq = [100, 500, 1000, 2500, 10000, 25000].map(channelPct);
    for (let i = 1; i < seq.length; i++) expect(seq[i]).toBeGreaterThan(seq[i - 1]);
  });

  it("зажата в 6..100", () => {
    expect(channelPct(10)).toBe(6);      // уже левого края шкалы
    expect(channelPct(100)).toBe(6);     // левый край — минимальная видимая полоска
    expect(channelPct(25000)).toBe(100);
    expect(channelPct(100000)).toBe(100); // правее шкалы не растёт
  });

  it("гигабит попадает примерно в середину — иначе шкала не логарифмическая", () => {
    // На линейной шкале 1000 из 25000 дало бы ~4%.
    expect(channelPct(1000)).toBeGreaterThan(35);
    expect(channelPct(1000)).toBeLessThan(50);
  });
});

describe("fmtChannel: подпись", () => {
  it("мегабиты до гигабита, дальше гигабиты", () => {
    expect(fmtChannel(100)).toBe("100 Мбит/с");
    expect(fmtChannel(500)).toBe("500 Мбит/с");
    expect(fmtChannel(1000)).toBe("1 Гбит/с");
    expect(fmtChannel(10000)).toBe("10 Гбит/с");
  });

  it("дробная часть только у некруглых значений", () => {
    expect(fmtChannel(2500)).toBe("2.5 Гбит/с");
  });
});

describe("fmtChannelShort: подпись сегмента", () => {
  it("короче полной формы, но однозначна", () => {
    expect(fmtChannelShort(100)).toBe("100М");
    expect(fmtChannelShort(1000)).toBe("1Г");
    expect(fmtChannelShort(2500)).toBe("2.5Г");
    expect(fmtChannelShort(10000)).toBe("10Г");
  });
});
