import { describe, it, expect } from "vitest";
import { sortHostings } from "./HostingsCatalog";
import type { Hosting } from "./api";

const h = (name: string, tags: string[] = [], price?: number): Hosting => ({
  id: name, name, website: "", notes: "", features: "",
  tags, media: [], tariffs: [], locations: [], asns: [],
  metrics: price === undefined ? {} : { price },
  created_at: 0,
} as unknown as Hosting);

describe("сортировка по совпадению тегов", () => {
  const list = [h("A", ["ddos", "ru"]), h("B", ["ddos"]), h("C", ["eu", "ddos", "ru"])];

  it("больше совпадений — выше", () => {
    // A и C дают по 2 совпадения, между собой — по имени.
    expect(sortHostings(list, "tags:desc", ["ddos", "ru"]).map(x => x.name))
      .toEqual(["A", "C", "B"]);
  });

  it("без выбранных тегов ранжировать нечем — порядок по имени", () => {
    // Совпадение считается ОТНОСИТЕЛЬНО набора; пустой набор даёт null у всех,
    // и карточки не должны прыгать случайным образом.
    expect(sortHostings(list, "tags:desc", []).map(x => x.name)).toEqual(["A", "B", "C"]);
  });

  it("не трогает исходный массив", () => {
    const before = list.map(x => x.name);
    sortHostings(list, "tags:desc", ["ru"]);
    expect(list.map(x => x.name)).toEqual(before);
  });
});

describe("сортировка по оценке", () => {
  it("неоценённые всегда внизу, в обе стороны", () => {
    const list = [h("нет оценки"), h("низкая", [], 10), h("высокая", [], 90)];
    expect(sortHostings(list, "price:desc", []).map(x => x.name))
      .toEqual(["высокая", "низкая", "нет оценки"]);
    expect(sortHostings(list, "price:asc", []).map(x => x.name))
      .toEqual(["низкая", "высокая", "нет оценки"]);
  });
});
