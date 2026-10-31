import * as React from "react";

import type { SymbolHit } from "@/lib/api";
import { GridPlaceholder } from "@/shell/GridPlaceholder";
import { StatusLine } from "@/shell/StatusLine";
import { TopBar } from "@/shell/TopBar";
import { useHealth } from "@/shell/useHealth";
import { VIEWS, ViewTabs, type ViewId } from "@/shell/ViewTabs";

/**
 * The shell: one screen, four bands, no routing.
 *
 *   top bar     identity + the global search (the only control that writes)
 *   tab strip   the nine views of plan §6.1 — a placeholder with real
 *               keyboard semantics so Phase 4 inherits a settled contract
 *   status line breadcrumb + GET /health
 *   content     the grid's exact box, empty and labelled
 *
 * State is view + selected symbol; everything else is read from the API. No
 * client-side store, because there is nothing yet worth caching and a store
 * would become a second source of truth the moment the grid lands.
 */
export function App() {
  const [view, setView] = React.useState<ViewId>("today");
  const [symbol, setSymbol] = React.useState<SymbolHit | null>(null);
  const health = useHealth();
  const searchRef = React.useRef<HTMLInputElement>(null);

  // "/" and ⌘K/ctrl-K focus the search box, the two chords every terminal
  // user already has in their hands. Ignored while typing so a "/" inside the
  // input stays a character.
  React.useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target;
      const typing =
        target instanceof HTMLElement &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);

      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
        return;
      }
      if (event.key === "/" && !typing) {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const active = VIEWS.find((entry) => entry.id === view) ?? VIEWS[0];

  return (
    <div className="flex h-dvh min-h-0 flex-col bg-bg">
      <TopBar onSelect={setSymbol} inputRef={searchRef} />

      <div className="border-b border-line bg-surface px-3 pt-0.5">
        <ViewTabs active={view} onChange={setView} />
      </div>

      <StatusLine
        viewLabel={active.label}
        symbol={symbol?.symbol ?? null}
        health={health}
      />

      <main className="flex min-h-0 flex-1 flex-col gap-2 p-3">
        <GridPlaceholder
          view={view}
          viewLabel={active.label}
          symbol={symbol?.symbol ?? null}
        />
      </main>

      <footer className="flex h-6 shrink-0 items-center justify-between border-t border-line bg-surface-sunken px-3 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
        <span>same-origin api only · no telemetry</span>
        <span>
          web v{__APP_VERSION__} · api v{health.report?.version ?? "—"}
        </span>
      </footer>
    </div>
  );
}
