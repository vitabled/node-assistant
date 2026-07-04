import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Test-only config (kept separate from vite.config.ts so the app build is
// untouched). jsdom + testing-library for the auth store/interceptor/components.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    css: false,
  },
});
