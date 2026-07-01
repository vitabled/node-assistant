import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: { mono: ["JetBrains Mono", "Fira Code", "monospace"] },
      colors: {
        terminal: {
          bg: "#0d1117",
          text: "#c9d1d9",
          border: "#30363d",
        },
      },
    },
  },
  plugins: [],
} satisfies Config;
