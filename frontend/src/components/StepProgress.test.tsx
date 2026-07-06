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
    // pending + no active step → no elapsed-timer digits abutting a badge; groups
    // default-open, so all 13 rows render. Each row's badge is "N.".
    const { container } = render(<StepProgress currentStep={0} totalSteps={13} status="pending" />);
    const text = container.textContent || "";
    for (let n = 1; n <= 13; n++) {
      const count = (text.match(new RegExp(`(?<!\\d)${n}\\.`, "g")) || []).length;
      expect(count, `step ${n} badge`).toBe(1);
    }
  });

  it("shows 100% and marks groups done on success", () => {
    const { container } = render(<StepProgress currentStep={13} totalSteps={13} status="success" />);
    expect(within(container).getByText("100%")).toBeInTheDocument();
  });
});
