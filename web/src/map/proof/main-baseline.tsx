/**
 * The bundle's control group: everything the map *shares* with the rest of the
 * app — the stylesheet, React, `cn` (clsx + tailwind-merge), the logo lane and
 * the route module — but no map. The difference between this bundle and the
 * map's is the map's marginal cost, which is the number that matters when the
 * shell imports the page.
 */

import { createRoot } from "react-dom/client";

import "@/styles/index.css";
import { LogoMark } from "@/grid/LogoMark";
import { Link } from "@/shell/routes";
import { cn } from "@/lib/utils";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <div className={cn("p-2", "text-fg")}>
      <Link to="/">Maps</Link>
      <LogoMark symbol="A" />
    </div>,
  );
}
