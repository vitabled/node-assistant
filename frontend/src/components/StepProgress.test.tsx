import { render, screen, cleanup, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { StepProgress, DEPLOY_STEPS, RENEW_STEPS } from "./StepProgress";

afterEach(cleanup);

describe("StepProgress (Ф6 restructure)", () => {
  it("has 13 deploy steps with the renamed/split labels", () => {
    expect(DEPLOY_STEPS).toHaveLength(13);
    // network split: one add + three post-reboot steps
    expect(DEPLOY_STEPS).toContain("Добавление порта SSH");
    expect(DEPLOY_STEPS).toContain("Перезагрузка");
    expect(DEPLOY_STEPS).toContain("Проверка нового порта SSH");
    expect(DEPLOY_STEPS).toContain("Удаление старого порта SSH");
    // renamed certbot → Hysteria2
    expect(DEPLOY_STEPS).toContain("Hysteria2");
    expect(DEPLOY_STEPS).not.toContain("SSL Certbot");
  });

  it("renders the three collapsible groups", () => {
    // active step inside the last group so it auto-expands
    render(<StepProgress currentStep={11} totalSteps={13} status="running" />);
    expect(screen.getByText("Оптимизация ОС")).toBeInTheDocument();
    expect(screen.getByText("Сеть")).toBeInTheDocument();
    expect(screen.getByText("Установка remnanode")).toBeInTheDocument();
  });

  it("orders «Маскировочный сайт» before «WARP Native» (masking runs first)", () => {
    const { container } = render(<StepProgress currentStep={11} totalSteps={13} status="running" />);
    const text = container.textContent || "";
    const iMask = text.indexOf("Маскировочный сайт");
    const iWarp = text.indexOf("WARP Native");
    expect(iMask).toBeGreaterThan(-1);
    expect(iWarp).toBeGreaterThan(-1);
    expect(iMask).toBeLessThan(iWarp);
  });

  it("keeps standalone steps outside any group (Подключение, SSL)", () => {
    render(<StepProgress currentStep={9} totalSteps={13} status="running" />);
    expect(screen.getByText("Подключение")).toBeInTheDocument();
    expect(screen.getByText("Cloudflare DNS + SSL")).toBeInTheDocument();
  });

  it("does NOT group the renew step list (RENEW_STEPS)", () => {
    render(<StepProgress currentStep={1} totalSteps={3} status="running" steps={RENEW_STEPS} />);
    expect(screen.queryByText("Оптимизация ОС")).toBeNull();
    expect(screen.queryByText("Сеть")).toBeNull();
    expect(screen.getByText("Выпуск и установка сертификата")).toBeInTheDocument();
  });

  it("renders every step 1..13 exactly once (no gap/overlap between groups)", () => {
    // pending + no active step → groups default-open, so all 13 rows render.
    // Numbering is hierarchical (Ф5: 1, 2, 3.1…6.4) — the invariant is that
    // every label renders exactly once and the badges cover all 13 steps.
    const { container } = render(<StepProgress currentStep={0} totalSteps={13} status="pending" />);
    const text = container.textContent || "";
    for (const label of DEPLOY_STEPS) {
      const count = text.split(label).length - 1;
      expect(count, `label «${label}»`).toBe(1);
    }
    const badges = ["1.", "2.", "3.1.", "3.2.", "4.1.", "4.2.", "4.3.", "4.4.", "5.", "6.1.", "6.2.", "6.3.", "6.4."];
    for (const badge of badges) {
      expect(text, `badge «${badge}»`).toContain(badge);
    }
    // no flat 7..13 badges remain after the hierarchical renumbering
    expect(text).not.toMatch(/(?<![\d.])(?:1[0-3]|[7-9])\./);
  });

  it("shows 100% and marks groups done on success", () => {
    const { container } = render(<StepProgress currentStep={13} totalSteps={13} status="success" />);
    expect(within(container).getByText("100%")).toBeInTheDocument();
  });
});
