import type { Config } from "tailwindcss";

/**
 * Tailwind-токены дизайн-системы Axottle.
 *
 * Источник значений — публичный CSS Axottle (`/root/work/axottle_transfer/design/all.css`)
 * и его разбор в `design-system.md` / `tokens.json` (там у каждого числа указан селектор-источник).
 * Живой панели Axottle у нас нет (релиз обфусцирован, установщик требует активации), поэтому это
 * чтение CSS, а не измерение скриншота.
 *
 * Палитра отдана ДВАЖДЫ, и это намеренно:
 *   • `axo.*`  — сырые hex Axottle (то, что реально написано в их таблицах стилей);
 *   • `bg/surface/line/ink/state/accent` — CSS-переменные `var(--axo-*)`, которые переключаются
 *     вместе с темой (`data-theme`) и скином (`data-skin="axottle"`, см. src/theme/axottle.css).
 * В новом коде использовать вторую половину: только она следует за темой.
 */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
        // Axottle: --font-manrope / --font-unbounded / "Google Sans Code" (см. src/fonts-axottle.css)
        sans: ["Manrope", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        display: ["Unbounded", "Manrope", "system-ui", "sans-serif"],
        axo: ["var(--axo-sans)"],
        "axo-mono": ["var(--axo-mono)"],
        "axo-display": ["var(--axo-display)"],
      },
      colors: {
        terminal: {
          bg: "#0d1117",
          text: "#c9d1d9",
          border: "#30363d",
        },
        // ── сырые hex Axottle: тёмная панель ──
        axo: {
          bg: "#08090a",
          sidebar: "#0d1014",
          surface: "#11161d",
          "surface-raised": "#171d25",
          "table-head": "#141a21",
          "surface-empty": "#0e1217",
          canvas: "#0b0f14",
          "canvas-dot": "#252e3a",
          line: "#1e2632",
          hairline: "#202933",
          "line-strong": "#2a3541",
          text: "#eaf1ed",
          muted: "#9aa8a2",
          nav: "#8e9a94",
          faint: "#7a8a80",   // Axottle raw #6a7770 (3.62:1 на bg3) → поднят до AA 4.66:1
          meta: "#647069",
          accent: "#f3a7c5",
          "accent-hi": "#f7d5e2", // их же --color-accent-soft как светлый тон
          "accent-ink": "#241018",
          ok: "#34d8a8",
          warn: "#f0b35a",
          bad: "#ff6b6b",
          info: "#77bdea",
        },
        // ── сырые hex Axottle: светлая «клиентская» тема ──
        "axo-light": {
          ink: "#241a20",
          muted: "#5b4751",
          faint: "#746270",
          canvas: "#f6f1f3",
          paper: "#fffafb",
          line: "#eadfe4",
          "line-strong": "#d9c8d0",
          olive: "#8f4968",
          "olive-soft": "#efc5d5",
          green: "#457d10",
          gold: "#8a6f1c",
          red: "#a8463f",
        },
        // ── тема-зависимые токены (var(--axo-*)) — предпочтительны в новом коде ──
        bg: {
          DEFAULT: "var(--bg0)",
          canvas: "var(--bg0)",
          sidebar: "var(--bg1)",
          surface: "var(--bg2)",
          raised: "var(--bg3)",
        },
        surface: {
          DEFAULT: "var(--axo-surface)",
          raised: "var(--axo-surface-raised)",
          "table-head": "var(--axo-surface-table-head)",
          empty: "var(--axo-surface-empty)",
        },
        line: {
          DEFAULT: "var(--line)",
          soft: "var(--line-soft)",
          strong: "var(--line-strong)",
        },
        ink: {
          DEFAULT: "var(--t-mid)",
          hi: "var(--t-hi)",
          mid: "var(--t-mid)",
          low: "var(--t-low)",
          faint: "var(--t-faint)",
        },
        accent: {
          DEFAULT: "var(--accent)",
          hi: "var(--accent-hi)",
          ink: "var(--accent-ink)",
          soft: "var(--axo-accent-soft)",
          dim: "var(--accent-dim)",
          line: "var(--accent-line)",
        },
        state: {
          ok: "var(--ok)",
          "ok-dim": "var(--ok-dim)",
          "ok-line": "var(--ok-line)",
          warn: "var(--warn)",
          "warn-dim": "var(--warn-dim)",
          "warn-line": "var(--warn-line)",
          bad: "var(--err)",
          "bad-dim": "var(--err-dim)",
          "bad-line": "var(--err-line)",
          info: "var(--axo-info)",
        },
      },
      // Шкала радиусов Axottle: чип 5px · элемент 8px · контрол 11px · карточка 18px · пилюля
      borderRadius: {
        axo: "var(--axo-r-md)",
        "axo-xs": "var(--axo-r-xs)",
        "axo-sm": "var(--axo-r-sm)",
        "axo-md": "var(--axo-r-md)",
        "axo-lg": "var(--axo-r-lg)",
        "axo-pill": "var(--axo-r-pill)",
      },
      boxShadow: {
        "axo-ring": "var(--axo-ring)",
        axo: "var(--axo-shadow-card)",
        "axo-pop": "var(--axo-shadow-pop)",
        "axo-toast": "var(--axo-shadow-toast)",
        "axo-dialog": "var(--axo-shadow-dialog)",
        "axo-panel": "var(--axo-shadow-panel)",
      },
      transitionTimingFunction: {
        axo: "var(--axo-ease)", // cubic-bezier(.22,1,.36,1)
      },
      transitionDuration: {
        "axo-fast": "150ms",
        "axo": "200ms",
        "axo-card": "450ms",
      },
      maxWidth: {
        "axo-content": "1400px",
      },
      spacing: {
        "axo-nav": "44px", // высота пункта меню: .platform-nav a{min-height:44px}
        "axo-topbar": "64px",
        "axo-sidebar": "248px",
      },
      // Движение из all.css @keyframes: portal-card-in / portal-progress / platform-shimmer
      keyframes: {
        "axo-card-in": {
          from: { opacity: "0", transform: "translateY(10px) scale(.99)" },
          to: { opacity: "1", transform: "none" },
        },
        "axo-fade": { from: { opacity: "0" }, to: { opacity: "1" } },
        "axo-progress": { from: { transform: "scaleX(0)" }, to: { transform: "scaleX(1)" } },
        "axo-shimmer": { to: { transform: "translateX(100%)" } },
      },
      animation: {
        "axo-card-in": "axo-card-in 450ms var(--axo-ease) both",
        "axo-fade": "axo-fade 180ms ease-out",
        "axo-progress": "axo-progress 700ms var(--axo-ease) both",
        "axo-shimmer": "axo-shimmer 1.4s infinite",
      },
    },
  },
  plugins: [],
} satisfies Config;
