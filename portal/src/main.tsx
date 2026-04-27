import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";

import "@/styles/index.css";

import App from "@/App";
import { AuthProvider } from "@/auth/AuthContext";
import { BrokerClientProvider } from "@/api/BrokerClientProvider";
import { queryClient } from "@/lib/queryClient";
import { initTheme } from "@/lib/theme";

// Synchronous theme application BEFORE the first React paint. This is
// what avoids the flash-of-wrong-theme on every page load for users
// whose preference is dark.
initTheme();

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("missing #root in index.html");
}

// Provider order matters:
//   QueryClientProvider — outermost, no upstream deps.
//   AuthProvider        — pages and BrokerClientProvider both consume it.
//   BrokerClientProvider — innermost of the three; reads auth via
//                          useAuth() to wire request headers.
createRoot(rootEl).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrokerClientProvider>
          <App />
        </BrokerClientProvider>
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
);
