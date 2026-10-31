import * as React from "react";

import { useViewCatalogue, type ViewDefinition } from "@/data/screens";
import { ErrorBoundary } from "@/grid/ErrorBoundary";
import { ScreenPanel } from "@/grid/ScreenPanel";
import { ViewTabs } from "@/grid/ViewTabs";
import { StatusLine } from "@/shell/StatusLine";
import { TopBar } from "@/shell/TopBar";
import { useHealth } from "@/shell/useHealth";

/**
 * The shell: one screen, four bands, no routing.
 *
 *   top bar     identity + the global search (the only control that writes)
 *   tab strip   the views `GET /screens/select/schema` served — nine panels,
 *               never a hardcoded list
 *   status line breadcrumb + GET /health
 *   content     the grid: 100 rows, widths solved to this container
 *
 * State is view + selected symbol. Rows, columns and the ETag belong to
 * `useScreens`, which is the only place that talks to `POST /screens/select`.
 */
export function App() {
  const catalogue = useViewCatalogue();
  const [view, setView] = React.useState("today");
  const [symbol, setSymbol] = React.useState<string | null>(null);
  const health = useHealth();
  const searchRef = React.useRef<HTMLInputElement>(null);

  // `all` is a destination (2 200 px, explicit horizontal scroll), not a tab:
  // the nine scannable panels are the strip. The schema serves both.
  const views = React.useMemo<ViewDefinition[]>(
    () => catalogue.views.filter((entry) => entry.name !== "all"),
    [catalogue.views],
  );

  React.useEffect(() => {
    if (views.length === 0) return;
    if (!views.some((entry) => entry.name === view)) setView(views[0]?.name ?? "today");
  }, [views, view]);

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

  const active = views.find((entry) => entry.name === view) ?? views[0] ?? null;

  return (
    <div className="flex h-dvh min-h-0 flex-col bg-bg">
      <TopBar onSelect={(hit) => setSymbol(hit.symbol)} inputRef={searchRef} />

      <div className="border-b border-line bg-surface px-3 pt-0.5">
        {views.length > 0 && active ? (
          <ViewTabs views={views} active={active.name} onChange={setView} />
        ) : (
          <div className="h-8" aria-hidden="true" />
        )}
      </div>

      <StatusLine viewLabel={active?.label ?? "Views"} symbol={symbol} health={health} />

      <main className="flex min-h-0 flex-1 flex-col gap-2 p-3">
        {catalogue.status === "error" ? (
          <section
            role="alert"
            className="flex min-h-0 flex-1 flex-col justify-center rounded-md border border-line border-l-2 border-l-alert bg-surface p-6"
          >
            <p className="text-ui font-medium text-fg">The view catalogue did not load</p>
            <p className="mt-1 max-w-[60ch] text-data text-fg-muted">{catalogue.error}</p>
            <p className="mt-1 max-w-[60ch] text-data text-fg-subtle">
              The grid renders columns the server names; without the catalogue there is
              nothing honest to draw.
            </p>
            <div className="mt-3">
              <button
                type="button"
                onClick={catalogue.retry}
                className="rounded-sm border border-line-strong px-2 py-1 text-ui text-fg-muted hover:text-fg"
              >
                Retry
              </button>
            </div>
          </section>
        ) : active ? (
          // A render fault in the grid costs the panel, never the shell: the
          // top bar, tabs and status line stay, and the boundary offers a way
          // back. API errors never arrive here — the hook renders those.
          <ErrorBoundary resetKey={active.name} label={`${active.label} panel`}>
            <ScreenPanel
              view={active}
              selectedSymbol={symbol}
              onActivate={setSymbol}
              onClear={() => setSymbol(null)}
            />
          </ErrorBoundary>
        ) : (
          <section
            aria-busy="true"
            aria-label="Loading views"
            className="flex min-h-0 flex-1 flex-col gap-1 rounded-md border border-line bg-surface p-3"
          >
            <div className="h-6 w-40 rounded-[1px] bg-surface-raised" />
            {Array.from({ length: 12 }, (_, index) => (
              <div
                key={index}
                className={`h-7 rounded-[1px] ${index % 2 === 0 ? "bg-row" : "bg-row-alt"}`}
              />
            ))}
          </section>
        )}
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
