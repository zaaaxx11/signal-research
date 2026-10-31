import * as React from "react";

import type { ViewDefinition } from "@/data/screens";
import { CommandPalette } from "@/filters/CommandPalette";
import { FilterDrawer } from "@/filters/FilterDrawer";
import { buildRegistry } from "@/filters/registry";
import { useFilterSchema } from "@/filters/schema";
import { useScreenState } from "@/filters/useScreenState";
import { ErrorBoundary } from "@/grid/ErrorBoundary";
import { ScreenPanel } from "@/grid/ScreenPanel";
import { ViewTabs } from "@/grid/ViewTabs";
import { StatusLine } from "@/shell/StatusLine";
import { TopBar } from "@/shell/TopBar";
import { useHealth } from "@/shell/useHealth";

/**
 * The shell: one screen, four bands, no routing.
 *
 *   top bar     identity + the symbol search
 *   tab strip   the views the schema served — never a hardcoded list
 *   status line breadcrumb + GET /health
 *   content     the chip bar (when filters exist) and the 100-row grid
 *
 * One schema read at boot feeds both the view catalogue and the filter
 * registry; one `useScreenState` owns view + sort + page + filters and writes
 * them to the query string. The drawer, the chips and the request body all read
 * that one state — there is no second copy to drift.
 */
export function App() {
  const schema = useFilterSchema();
  const registry = React.useMemo(
    () => (schema.schema ? buildRegistry(schema.schema) : null),
    [schema.schema],
  );

  // `all` is a destination (116 columns, explicit horizontal scroll), not a
  // tab: the nine scannable panels are the strip. The schema serves both.
  const views = React.useMemo<ViewDefinition[]>(
    () => (schema.schema?.views ?? []).filter((entry) => entry.name !== "all"),
    [schema.schema],
  );
  const viewNames = React.useMemo(() => views.map((entry) => entry.name), [views]);

  const screen = useScreenState(registry, viewNames);
  const [symbol, setSymbol] = React.useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = React.useState(false);
  const [paletteOpen, setPaletteOpen] = React.useState(false);
  const [focusKey, setFocusKey] = React.useState<string | null>(null);
  const health = useHealth();
  const searchRef = React.useRef<HTMLInputElement>(null);

  // ⌘K is the filter palette; "/" stays the symbol search. Two chords, two
  // jobs — the previous binding sent both to the same input.
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
        setPaletteOpen(true);
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

  const active = views.find((entry) => entry.name === screen.state.view) ?? views[0] ?? null;

  const openDrawer = () => {
    setFocusKey(null);
    setDrawerOpen(true);
  };

  const closeDrawer = () => {
    setDrawerOpen(false);
    setFocusKey(null);
  };

  return (
    <div className="flex h-dvh min-h-0 flex-col bg-bg">
      <TopBar onSelect={(hit) => setSymbol(hit.symbol)} inputRef={searchRef} />

      <div className="border-b border-line bg-surface px-3 pt-0.5">
        {views.length > 0 && active ? (
          <ViewTabs views={views} active={active.name} onChange={screen.setView} />
        ) : (
          <div className="h-8" aria-hidden="true" />
        )}
      </div>

      <StatusLine viewLabel={active?.label ?? "Views"} symbol={symbol} health={health} />

      <main className="flex min-h-0 flex-1 flex-col gap-2 p-3">
        {schema.status === "error" ? (
          <section
            role="alert"
            className="flex min-h-0 flex-1 flex-col justify-center rounded-md border border-line border-l-2 border-l-alert bg-surface p-6"
          >
            <p className="text-ui font-medium text-fg">The filter schema did not load</p>
            <p className="mt-1 max-w-[60ch] text-data text-fg-muted">{schema.error}</p>
            <p className="mt-1 max-w-[60ch] text-data text-fg-subtle">
              Views, columns and filter keys all come from that one document; without it
              there is nothing honest to draw.
            </p>
            <div className="mt-3">
              <button
                type="button"
                onClick={schema.retry}
                className="rounded-sm border border-line-strong px-2 py-1 text-ui text-fg-muted hover:text-fg"
              >
                Retry
              </button>
            </div>
          </section>
        ) : active && registry && screen.ready ? (
          // A render fault in the grid costs the panel, never the shell: the
          // top bar, tabs and status line stay, and the boundary offers a way
          // back. API errors never arrive here — the hook renders those.
          <ErrorBoundary resetKey={active.name} label={`${active.label} panel`}>
            <ScreenPanel
              view={active}
              selectedSymbol={symbol}
              onActivate={setSymbol}
              onClear={() => setSymbol(null)}
              sort={screen.state.sort}
              page={screen.state.page}
              onSortChange={screen.setSort}
              onPageChange={screen.setPage}
              filters={screen.state.filters}
              registry={registry}
              onRemoveFilter={screen.removeFilter}
              onClearFilters={screen.clearFilters}
              onOpenFilters={openDrawer}
              filterNotice={screen.notice}
              onDismissFilterNotice={screen.dismissDropped}
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

      {registry ? (
        <FilterDrawer
          open={drawerOpen}
          registry={registry}
          filters={screen.state.filters}
          onClose={closeDrawer}
          onAdd={screen.addFilters}
          onRemove={screen.removeFilter}
          onClear={screen.clearFilters}
          onClearKey={(key) =>
            screen.setFilters(screen.state.filters.filter((filter) => filter.key !== key))
          }
          onApplyBucket={screen.applyBucket}
          focusKey={focusKey}
        />
      ) : null}

      {registry ? (
        <CommandPalette
          open={paletteOpen}
          registry={registry}
          onClose={() => setPaletteOpen(false)}
          onPick={(key) => {
            setPaletteOpen(false);
            setFocusKey(key);
            setDrawerOpen(true);
          }}
        />
      ) : null}

      <footer className="flex h-6 shrink-0 items-center justify-between border-t border-line bg-surface-sunken px-3 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
        <span>same-origin api only · no telemetry</span>
        <span>
          web v{__APP_VERSION__} · api v{health.report?.version ?? "—"}
        </span>
      </footer>
    </div>
  );
}
