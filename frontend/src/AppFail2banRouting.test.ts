import { describe, expect, it } from "vitest";
import appSource from "./App.tsx?raw";

describe("Fail2Ban section routing", () => {
  it("renders the list in Fail2Ban and not inside SSL management", () => {
    const fail2banStart = appSource.indexOf('{tab === "f2b-list"');
    const certsStart = appSource.indexOf('{tab === "certs"');
    const screenEnd = appSource.indexOf("</Screen>", certsStart);

    expect(fail2banStart).toBeGreaterThan(-1);
    expect(certsStart).toBeGreaterThan(fail2banStart);
    expect(appSource.slice(fail2banStart, certsStart)).toContain("<F2bList />");
    expect(appSource.slice(certsStart, screenEnd)).not.toContain("<F2bList />");
  });
});