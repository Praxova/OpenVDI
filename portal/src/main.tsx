import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";

import "@/styles/index.css";

import App from "@/App";
import { queryClient } from "@/lib/queryClient";

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("missing #root in index.html");
}

createRoot(rootEl).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
