import "../../design/tokens.css";
import "../../design/motion.css";
import "./global.css";

import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { createQueryClient } from "./api/hooks";
import { App } from "./App";
import { DraftProvider } from "./draft/DraftContext";

const root = document.getElementById("root");
if (!root) throw new Error("index.html has no #root element");

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={createQueryClient()}>
      <DraftProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </DraftProvider>
    </QueryClientProvider>
  </StrictMode>,
);
