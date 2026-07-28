import { describe, it, expect } from "vitest";
import { buildGroups } from "./storePicker";

describe("раскладка сторов по разделам", () => {
  it("подставляет человеческие названия вместо имён файлов", () => {
    const g = buildGroups(["hostings.json"], []);
    expect(g.flatMap(x => x.items)).toEqual([{ id: "hostings.json", label: "Хостинги (каталог)" }]);
  });

  it("секции настроек приходят с префиксом и получают своё название", () => {
    const items = buildGroups([], ["haproxy"]).flatMap(x => x.items);
    expect(items).toEqual([{ id: "settings:haproxy", label: "HAProxy (NodeFlow)" }]);
  });

  it("незнакомый стор не теряется, а падает в «Не разложено»", () => {
    // Бэкенд может завести новый стор раньше, чем раскладка о нём узнает —
    // молча спрятать его значило бы потерять данные при экспорте «выбранного».
    const g = buildGroups(["hostings.json", "новый_стор.json"], []);
    const rest = g.find(x => x.title === "Не разложено");
    expect(rest?.items).toEqual([{ id: "новый_стор.json", label: "новый_стор.json" }]);
  });

  it("пустые группы не показываются", () => {
    const g = buildGroups(["hostings.json"], []);
    expect(g).toHaveLength(1);
    expect(g[0].title).toBe("Инфраструктура и справка");
  });
});
