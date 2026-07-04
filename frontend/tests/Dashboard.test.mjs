// Regression test for the Dashboard white-screen crash.
//
// Bug: /api/checker/statuspage can return `container:"running"` with an EMPTY
// `global:{}` (checker up but unreachable — `reachable:false`, `error` set).
// The declared type says `global: Global` (always full), so the component
// indexed BANNER[g.state===undefined] and read `.cls` off undefined, taking
// down the whole React tree (no error boundary) → blank "Дешборд" tab.
//
// This project has no test runner, so the test is a framework-free node script
// that mirrors the three guard expressions from Dashboard.tsx (lines ~121, 174,
// 180-181). It re-models the guards rather than importing the .tsx component
// (no jsdom/tsx loader in this repo), so keep it in sync with those lines.
//
// Run: npm test  (from frontend/)

import assert from "node:assert/strict";

const BANNER = { ok: { cls: "ok" }, partial: { cls: "p" }, down: { cls: "d" }, unknown: { cls: "u" } };

// Mirrors the guarded render path after the fix. Must NOT throw for any input.
function render(data) {
  const g = data?.global;
  const running = data?.container === "running";
  const state = running && g?.state ? g.state : "unknown";   // line 121
  const banner = BANNER[state];
  const cls = banner.cls;                                     // line 168 (crashed before)
  const subtitle = running && g?.state                       // line 174
    ? `${g.online} из ${g.total} узлов онлайн`
    : "fallback";
  const protoVal = g?.protocols ? String(g.protocols.length) : "—";  // line 180
  const protoSub = g?.protocols?.join(", ");                          // line 181
  return { state, cls, subtitle, protoVal, protoSub };
}

let passed = 0;
function test(name, fn) {
  try { fn(); passed++; console.log(`  ok  ${name}`); }
  catch (e) { console.error(`FAIL  ${name}\n      ${e.message}`); process.exitCode = 1; }
}

// malformed-input / empty: the exact bug — running but global is {}.
test("test_empty_global_running_degrades_to_unknown", () => {
  const r = render({ container: "running", reachable: false, global: {}, error: "unreachable" });
  assert.equal(r.state, "unknown");
  assert.equal(r.cls, "u");
  assert.equal(r.protoVal, "—");
  assert.equal(r.protoSub, undefined);
});

// external-failure: checker container up but HTTP bridge dead (reachable:false).
test("test_unreachable_checker_no_crash", () => {
  const r = render({ container: "running", reachable: false, global: {}, error: "ECONNREFUSED" });
  assert.equal(r.state, "unknown");
});

// deleted-resource: container not running at all → unknown banner, no throw.
test("test_container_not_running", () => {
  const r = render({ container: "stopped", reachable: false, global: {} });
  assert.equal(r.state, "unknown");
  assert.equal(r.cls, "u");
});

// null-field: global present, uptime/protocols valid but online counts zero.
test("test_full_global_happy_path", () => {
  const r = render({ container: "running", reachable: true,
    global: { state: "ok", uptime30d: 99.9, protocols: ["vless", "trojan"], total: 4, online: 4, offline: 0 } });
  assert.equal(r.state, "ok");
  assert.equal(r.cls, "ok");
  assert.equal(r.subtitle, "4 из 4 узлов онлайн");
  assert.equal(r.protoVal, "2");
  assert.equal(r.protoSub, "vless, trojan");
});

// boundary: global present with an EMPTY protocols array and zero totals.
test("test_boundary_empty_protocols", () => {
  const r = render({ container: "running", reachable: true,
    global: { state: "down", uptime30d: null, protocols: [], total: 0, online: 0, offline: 0 } });
  assert.equal(r.state, "down");
  assert.equal(r.protoVal, "0");
  assert.equal(r.protoSub, "");
});

// null data (initial load, before first fetch resolves).
test("test_null_data", () => {
  const r = render(null);
  assert.equal(r.state, "unknown");
  assert.equal(r.cls, "u");
});

console.log(`\n${passed} passed`);
