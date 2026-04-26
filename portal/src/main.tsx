import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "@/styles/index.css";

import App from "@/App";

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("missing #root in index.html");
}

createRoot(rootEl).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
