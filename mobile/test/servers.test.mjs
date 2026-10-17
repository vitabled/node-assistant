import { test } from "node:test";
import assert from "node:assert/strict";
import { normalizeUrl, loadServers, saveServers, upsertServer, removeServer, autoOpenTarget, AUTO_KEY } from "../www/servers.js";

const mem = () => { const m = new Map(); return { getItem: k => m.get(k) ?? null, setItem: (k, v) => m.set(k, String(v)) }; };

test("normalizeUrl: схема по умолчанию https, путь отбрасывается", () => {
  assert.equal(normalizeUrl("panel.example.com"), "https://panel.example.com");
  assert.equal(normalizeUrl(" http://1.2.3.4:8080/app/ "), "http://1.2.3.4:8080");
  assert.equal(normalizeUrl("https://Panel.Example.com/"), "https://panel.example.com");
});

test("normalizeUrl: мусор и чужие схемы отклоняются", () => {
  for (const bad of ["", "   ", "ftp://x.com", "javascript://alert(1)", "http://", null]) assert.equal(normalizeUrl(bad), null, String(bad));
});

test("upsertServer: без дублей, последний — первым, имя сохраняется", () => {
  let l = upsertServer([], "a.com", "Прод");
  l = upsertServer(l, "b.com", "");
  l = upsertServer(l, "https://a.com/x", "");
  assert.deepEqual(l, [{ url: "https://a.com", name: "Прод" }, { url: "https://b.com", name: "b.com" }]);
  assert.throws(() => upsertServer(l, "ftp://z", ""));
  assert.deepEqual(removeServer(l, "https://a.com"), [{ url: "https://b.com", name: "b.com" }]);
});

test("loadServers: битое хранилище не роняет приложение", () => {
  const s = mem();
  s.setItem("na_mobile_servers", "{oops");
  assert.deepEqual(loadServers(s), []);
  s.setItem("na_mobile_servers", JSON.stringify([{ url: "https://ok.com", name: "ok" }, { url: "ftp://bad" }, 5]));
  assert.deepEqual(loadServers(s), [{ url: "https://ok.com", name: "ok" }]);
});

test("autoOpenTarget: только при включённом флаге и без ?setup", () => {
  const s = mem();
  saveServers(s, [{ url: "https://a.com", name: "a" }]);
  assert.equal(autoOpenTarget(s, ""), null);
  s.setItem(AUTO_KEY, "1");
  assert.equal(autoOpenTarget(s, ""), "https://a.com");
  assert.equal(autoOpenTarget(s, "?setup=1"), null);
});
