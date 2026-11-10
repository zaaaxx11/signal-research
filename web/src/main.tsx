import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// One-time localStorage migration: Ztockz → Signal Research keys.
// Must run before any store reads its key, so it sits above the first import
// that touches storage.
import "./lib/migrateStorage";

import { App } from "./app";
import "./styles/index.css";

const container = document.getElementById("root");
if (container === null) {
  throw new Error('index.html is missing <div id="root">');
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
