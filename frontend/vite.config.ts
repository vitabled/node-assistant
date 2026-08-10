import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Backend для /api и /ws. На этой машине :8000 занят другим приложением
// (AI-Router Admin API), поэтому адрес переопределяется переменной
// NI_BACKEND — см. start-dev.bat (локальный backend на :8010).
const backend = process.env.NI_BACKEND || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      "/api": { target: backend, changeOrigin: true },
      "/ws": { target: backend.replace(/^http/, "ws"), ws: true, changeOrigin: true },
    },
  },
});
