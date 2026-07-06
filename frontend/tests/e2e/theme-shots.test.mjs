import { test } from "node:test";
import assert from "node:assert/strict";
import { apiStub } from "./theme-shots.mjs";

// Runs via `node --test tests/e2e/theme-shots.test.mjs` (outside vitest, whose
// include is scoped to src/**). The screenshot harness stubs /api/** so screens
// render without a backend; these assert the stub returns the shapes each screen
// reads, so a field rename can't silently produce blank/crashing screenshots.

test("settings shape the deploy form + settings tabs read", () => {
  const s = apiStub("http://x/api/settings");
  assert.equal(s.deploy_defaults.current_ssh_port, 22);
  assert.equal(s.deploy_defaults.remnanode_port, 2222);
  assert.ok(s.remnawave);
  assert.equal(s.xray_checker.enabled, false);
});

test("list endpoints return arrays", () => {
  for (const u of ["/node-plugins", "/squads/internal", "/remnawave/nodes", "/templates", "/traffic-rules"]) {
    assert.ok(Array.isArray(apiStub("http://x/api" + u)), u);
  }
});

test("statuspage is guarded (empty global, so Dashboard's g?.state guard holds)", () => {
  const sp = apiStub("http://x/api/checker/statuspage?ticks=30");
  assert.deepEqual(sp.global, {});
  assert.ok(Array.isArray(sp.nodes));
  assert.ok(Array.isArray(apiStub("http://x/api/checker/incidents")));
});

test("infra-billing list endpoints return arrays; summary/settings are objects", () => {
  for (const u of ["/infra-billing/providers", "/infra-billing/projects", "/infra-billing/services",
                   "/infra-billing/payments", "/infra-billing/api-tokens", "/subscriptions/status"]) {
    assert.ok(Array.isArray(apiStub("http://x/api" + u)), u);
  }
  assert.equal(apiStub("http://x/api/infra-billing/dashboard/summary").base_currency, "RUB");
  assert.equal(apiStub("http://x/api/infra-billing/settings").base_currency, "RUB");
});

test("unknown endpoints fall back to an empty object", () => {
  assert.deepEqual(apiStub("http://x/api/whatever"), {});
});
