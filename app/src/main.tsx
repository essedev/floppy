import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { isTauri } from "@tauri-apps/api/core";
import { App } from "@/App";
import { backend } from "@/lib/backend";
import "@/styles.css";

const view = backend.view();
document.body.dataset.view = view;
// Outside Tauri the views are looked at in a browser: the panel gets a page
// around it instead of a transparent window.
document.body.dataset.host = isTauri() ? "app" : "browser";

const queries = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false } },
});

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <QueryClientProvider client={queries}>
      <App view={view} />
    </QueryClientProvider>
  </React.StrictMode>,
);
