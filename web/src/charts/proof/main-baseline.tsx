/**
 * The control: the dependencies the shell already ships — React, the
 * stylesheet, `cn` and the formatters Charts also uses — with no chart code.
 * The difference between this bundle and `main-network.tsx` is Charts'
 * marginal gzip cost.
 */

import { createRoot } from "react-dom/client";

import "@/styles/index.css";
import { formatPrice, formatVolume } from "@/home/signals";
import { cn } from "@/lib/utils";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <div className={cn("p-2", "text-fg")}>
      {formatPrice(5900.12)} {formatVolume(1_500_000)}
    </div>,
  );
}
