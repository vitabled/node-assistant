import React from "react";
import ReactDOM from "react-dom/client";
import { AuthGate } from "./auth/AuthGate";
import { installApiClient } from "./auth/apiClient";
import "./index.css";

// Attach the bearer-token interceptor before any component can fire a request.
installApiClient();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AuthGate />
  </React.StrictMode>
);
