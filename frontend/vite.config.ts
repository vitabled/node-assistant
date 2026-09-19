import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Backend для /api и /ws. На этой машине :8000 занят другим приложением
// (AI-Router Admin API), поэтому адрес переопределяется переменной
// NI_BACKEND — см. start-dev.bat (локальный backend на :8010).
const backend = process.env.NI_BACKEND || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // React выносится в отдельный хешированный чанк: он не меняется между
        // деплоями панели, поэтому на повторных загрузках перекачивается только
        // код приложения (637 КБ), а не весь бандл целиком (827 КБ).
        manualChunks(id: string) {
          if (id.includes("node_modules/react/") || id.includes("node_modules/react-dom/") ||
              id.includes("node_modules/scheduler/")) return "vendor-react";
          return undefined;
        },
      },
    },
  },
  server: {
    port: 3000,
    proxy: {
      "/api": { target: backend, changeOrigin: true },
      "/ws": { target: backend.replace(/^http/, "ws"), ws: true, changeOrigin: true },
    },
  },
});
