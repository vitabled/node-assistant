import React from "react";
import ReactDOM from "react-dom/client";
import { AuthGate } from "./auth/AuthGate";
import { installApiClient } from "./auth/apiClient";
// Шрифты self-hosted (см. src/fonts.css): внешний fonts.googleapis.com больше не нужен.
import "./fonts.css";
// Axottle typography (Manrope / Unbounded, см. scripts/vendor-axo-fonts.mjs) — тоже self-hosted.
import "./fonts-axottle.css";
import "./index.css";
// Дизайн-слой Axottle — ПОСЛЕ index.css: палитры скина `axottle` идут на той же
// специфичности, что палитры остальных скинов, поэтому порядок решает.
import "./theme/axottle.css";

// Attach the bearer-token interceptor before any component can fire a request.
installApiClient();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AuthGate />
  </React.StrictMode>
);
