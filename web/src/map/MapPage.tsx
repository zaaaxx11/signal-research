/**
 * Maps: the whole US market, in four views.
 *
 * Views: TREEMAP (canvas treemap), MATRIX (colored square grid), BUBBLE
 * (scatter plot), GRID (uniform card grid sorted by cap).
 *
 * Sidebar: view tabs, SIZE BY toggle, MAP FILTER checkboxes, timeframe
 * select, search input filtering a ticker list, each row links to /stock/{sym}.
 * Tooltip: DOM div pointer-events:none on hover showing symbol,name,price,change%.
 * Legend: color scale (-3% to +3%) + SIZE BY label.
 */

import * as React from "react";

import { ErrorState } from "@/components/ErrorState";
import { SectionTitle } from "@/components/SectionTitle";
import { notify } from "@/lib/notify";
import { cn } from "@/lib/utils";
import { Link } from "@/shell/routes";

import { BubbleView } from "./BubbleView";
import { clockLabel, formatCount, signedPercent, toneClass } from "./format";
import { GridView } from "./GridView";
import { MapCanvas, type MapCanvasStats } from "./MapCanvas";
import { MatrixView } from "./MatrixView";
import { readTheme, swatch } from "./theme";
import { sectorCap } from "./types";
import {
  useMapData,
  useMarketGroups,
  TIMEFRAMES,
  TIMEFRAME_LABELS,
  TIMEFRAME_METRIC_LABELS,
  type Timeframe,
} from "./useMapData";

// ── map filter checkboxes ──
const MAP_FILTERS = [
  { value: "stocks", label: "Stocks" },
  { value: "futures", label: "Futures" },
  { value: "forex", label: "Forex" },
  { value: "crypto", label: "Crypto" },
] as const;

// ── filter maps select options ──
const FILTER_MAPS_OPTIONS = [
  { value: "all", label: "All Sectors" },
  { value: "Technology", label: "Technology" },
  { value: "Financial", label: "Financial" },
  { value: "Healthcare", label: "Healthcare" },
  { value: "Consumer Cyclical", label: "Consumer Cyclical" },
  { value: "Industrials", label: "Industrials" },
  { value: "Energy", label: "Energy" },
  { value: "Utilities", label: "Utilities" },
  { value: "Real Estate", label: "Real Estate" },
  { value: "Basic Materials", label: "Basic Materials" },
  { value: "Communication", label: "Communication Services" },
  { value: "Consumer Defensive", label: "Consumer Defensive" },
] as const;

type ViewId = "treemap" | "matrix" | "bubble" | "grid";

const VIEWS: readonly { id: ViewId; label: string }[] = [
  { id: "treemap", label: "Treemap" },
  { id: "matrix", label: "Matrix" },
  { id: "bubble", label: "Bubble" },
  { id: "grid", label: "Grid" },
];

export interface MapPageProps {
  readonly region?: string;
  readonly onOpenSymbol?: (symbol: string) => void;
}

// ── Legend bar: color scale -3% to +3% ──
function LegendBar({ theme }: { theme: ReturnType<typeof readTheme> }) {
  const steps = [
    { label: "-3%", color: swatch(-3, theme) },
    { label: "-2%", color: swatch(-2, theme) },
    { label: "-1%", color: swatch(-1, theme) },
    { label: "0%", color: swatch(0, theme) },
    { label: "+1%", color: swatch(1, theme) },
    { label: "+2%", color: swatch(2, theme) },
    { label: "+3%", color: swatch(3, theme) },
  ];
  return (
    <div className="flex items-center gap-0.5">
      {steps.map((step) => (
        <div
          key={step.label}
          className="flex-1 rounded-[1px] text-center font-mono text-[9px] leading-tight text-fg mix-blend-difference"
          style={{ background: step.color, height: 14, minWidth: 28 }}
        >
          {step.label}
        </div>
      ))}
    </div>
  );
}

// ── Skeleton ──
function Skeleton() {
  return (
    <div
      data-testid="map-loading"
      aria-busy="true"
      className="flex min-h-0 flex-1 flex-col gap-3 p-4"
    >
      <div className="h-3 w-2/3 animate-pulse rounded-sm bg-accent-soft" />
      <div className="h-3 w-1/2 animate-pulse rounded-sm bg-accent-soft" />
      <div className="mt-2 flex-1 animate-pulse rounded-sm bg-accent-soft" />
      <p className="text-data text-fg-subtle">Reading the market map…</p>
    </div>
  );
}

export function MapPage({ region: _region, onOpenSymbol }: MapPageProps) {
  const [timeframe, setTimeframe] = React.useState<Timeframe>("1d");
  const [view, setView] = React.useState<ViewId>("treemap");
  const [sidebarOpen, setSidebarOpen] = React.useState(true);
  const [search, setSearch] = React.useState("");
  const [activeFilters, setActiveFilters] = React.useState<Set<string>>(new Set(["stocks"]));
  const [filterMap, setFilterMap] = React.useState("all");
  const [sizeBy, setSizeBy] = React.useState<"cap" | "equal">("cap");

  const mapView = useMapData({ timeframe });
  const { groups } = useMarketGroups();
  const theme = React.useMemo(() => readTheme(), []);
  const [stats, setStats] = React.useState<MapCanvasStats | null>(null);

  const onStats = React.useCallback((next: MapCanvasStats) => {
    setStats((prev) =>
      prev !== null && prev.drawn === next.drawn && prev.omitted === next.omitted
        ? prev
        : next,
    );
  }, []);

  const payload = mapView.payload;
  const metricLabelText = TIMEFRAME_METRIC_LABELS[timeframe] ?? "Performance";

  // Sectors sorted by cap
  const sectors = React.useMemo(() => {
    if (payload === null) return [];
    return [...payload.sectors].sort((a, b) => {
      const c = sectorCap(b) - sectorCap(a);
      return c !== 0 ? c : a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
    });
  }, [payload]);

  // Flattened stocks for sidebar list
  const allStocks = React.useMemo(() => {
    if (payload === null) return [];
    return payload.sectors
      .flatMap((s) => [...s.stocks])
      .sort((a, b) => b.cap - a.cap);
  }, [payload]);

  const filteredStocks = React.useMemo(() => {
    if (search.trim() === "") return allStocks;
    const q = search.trim().toLowerCase();
    return allStocks.filter(
      (s) =>
        s.symbol.toLowerCase().includes(q) ||
        (s.name && s.name.toLowerCase().includes(q)),
    );
  }, [allStocks, search]);

  const totalSectors = sectors.length;
  const totalStocks = payload?.mapped ?? 0;

  // Notify on error
  React.useEffect(() => {
    if (mapView.status === "error" && mapView.error) {
      notify.error("Map failed to load", mapView.error);
    }
  }, [mapView.status, mapView.error]);

  const ready = mapView.status === "ready" && payload !== null;

  return (
    <main
      data-testid="map-page"
      data-state={mapView.status}
      className="flex min-h-0 flex-1 flex-col bg-[#000] text-fg"
    >
      {/* Section title */}
      <div className="shrink-0 px-3 pt-3">
        <SectionTitle variant={4}>
          {formatCount(totalStocks)} stocks · {formatCount(totalSectors)} sectors
        </SectionTitle>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* Sidebar toggle bar */}
        <button
          type="button"
          data-testid="sidebar-toggle"
          onClick={() => setSidebarOpen((o) => !o)}
          className="flex shrink-0 items-center justify-center border-r border-line bg-surface px-1 text-fg-muted hover:text-fg"
          aria-label={sidebarOpen ? "Collapse sidebar" : "Expand sidebar"}
        >
          {sidebarOpen ? "◀" : "▶"}
        </button>

        {/* Sidebar */}
        {sidebarOpen ? (
          <aside
            data-testid="map-sidebar"
            className="flex w-[240px] shrink-0 flex-col gap-2 overflow-y-auto border-r border-line bg-surface p-2"
          >
            {/* View tabs */}
            <div
              role="group"
              aria-label="Map view"
              className="flex gap-px rounded-sm border border-line p-0.5"
            >
              {VIEWS.map((v) => (
                <button
                  key={v.id}
                  type="button"
                  data-testid={`map-view-${v.id}`}
                  aria-pressed={view === v.id}
                  onClick={() => setView(v.id)}
                  className={cn(
                    "flex-1 rounded-sm px-1.5 py-1 font-mono text-label uppercase tracking-[0.06em] transition-colors",
                    view === v.id
                      ? "bg-row-selected text-fg"
                      : "text-fg-subtle hover:text-fg",
                  )}
                >
                  {v.label}
                </button>
              ))}
            </div>

            {/* Size by */}
            <div>
              <p className="text-label uppercase tracking-[0.1em] text-fg-subtle">
                Size by
              </p>
              <div className="mt-1 flex gap-px rounded-sm border border-line p-0.5">
                {(["cap", "equal"] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    data-testid={`map-size-${mode}`}
                    aria-pressed={sizeBy === mode}
                    onClick={() => setSizeBy(mode)}
                    className={cn(
                      "flex-1 rounded-sm px-1.5 py-0.5 font-mono text-label uppercase tracking-[0.06em] transition-colors",
                      sizeBy === mode
                        ? "bg-row-selected text-fg"
                        : "text-fg-subtle hover:text-fg",
                    )}
                  >
                    {mode === "cap" ? "Market Cap" : "Equal"}
                  </button>
                ))}
              </div>
            </div>

            {/* Map filter */}
            <div>
              <p className="text-label uppercase tracking-[0.1em] text-fg-subtle">
                Map filter
              </p>
              <div className="mt-1 flex gap-px rounded-sm border border-line p-0.5">
                {MAP_FILTERS.map((f) => {
                  const on = activeFilters.has(f.value);
                  return (
                    <button
                      key={f.value}
                      type="button"
                      data-testid={`map-filter-${f.value}`}
                      aria-pressed={on}
                      onClick={() =>
                        setActiveFilters((prev) => {
                          const next = new Set(prev);
                          if (next.has(f.value)) next.delete(f.value);
                          else next.add(f.value);
                          return next;
                        })
                      }
                      className={cn(
                        "flex-1 rounded-sm px-2 py-0.5 font-mono text-label uppercase tracking-[0.06em] transition-colors",
                        on ? "bg-row-selected text-fg" : "text-fg-subtle hover:text-fg",
                      )}
                    >
                      {f.label}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Filter maps — sector/industry select */}
            <div>
              <p className="text-label uppercase tracking-[0.1em] text-fg-subtle">
                Filter maps
              </p>
              <select
                data-testid="filter-maps-select"
                value={filterMap}
                onChange={(e) => setFilterMap(e.target.value)}
                className="mt-1 w-full rounded-sm border border-line bg-surface px-2 py-1 font-mono text-label text-fg"
              >
                {FILTER_MAPS_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>

                        {/* Sector filter */}
                        <div className="min-h-0">
                          <p className="text-label uppercase tracking-[0.1em] text-fg-subtle">
                            Sector filter
                          </p>
                          <div className="mt-1 max-h-[180px] space-y-0.5 overflow-y-auto">
                            {groups.map((g) => (
                              <label
                                key={g.id}
                                data-testid={`map-filter-group-${g.id}`}
                                className="flex cursor-pointer items-center gap-1.5 rounded-sm px-1 py-0.5 text-data text-fg hover:bg-row-hover"
                              >
                                <input
                                  type="checkbox"
                                  defaultChecked={g.id === "Technology"}
                                  onChange={() => { /* sector filter — client-side only until backend supports per-sector map */ }}
                                  className="size-3 accent-brand"
                                />
                    <span className="flex-1 truncate">{g.label}</span>
                    <span className="font-mono text-label text-fg-subtle">
                      {formatCount(g.count)}
                    </span>
                  </label>
                ))}
              </div>
            </div>

            {/* Timeframe */}
            <div>
              <p className="text-label uppercase tracking-[0.1em] text-fg-subtle">
                Timeframe
              </p>
              <select
                data-testid="map-timeframe-select"
                value={timeframe}
                onChange={(e) => setTimeframe(e.target.value as Timeframe)}
                className="mt-1 w-full rounded-sm border border-line bg-surface px-2 py-1 font-mono text-label text-fg"
              >
                {TIMEFRAMES.map((tf) => (
                  <option key={tf} value={tf}>
                    {TIMEFRAME_LABELS[tf]}
                  </option>
                ))}
              </select>
            </div>

            {/* Search */}
            <div>
              <input
                type="search"
                data-testid="map-search"
                placeholder="Filter stocks…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full rounded-sm border border-line bg-surface px-2 py-1 font-mono text-label text-fg placeholder:text-fg-subtle"
              />
            </div>

            {/* Ticker list */}
            {search.trim().length > 0 ? (
              <div className="min-h-0 flex-1 overflow-y-auto">
                <p className="mb-1 text-label uppercase tracking-[0.1em] text-fg-subtle">
                  {formatCount(filteredStocks.length)} matching
                </p>
                <ul className="space-y-px">
                  {filteredStocks.slice(0, 200).map((stock) => {
                    const sp = signedPercent(stock.change);
                    return (
                      <li key={stock.symbol}>
                        <Link
                          to={`/stock/${encodeURIComponent(stock.symbol)}`}
                          data-testid={`map-ticker-${stock.symbol}`}
                          className="flex items-center justify-between rounded-sm px-1 py-0.5 text-data hover:bg-row-hover"
                        >
                          <span className="font-mono font-medium text-fg">
                            {stock.symbol}
                          </span>
                          <span
                            className={cn(
                              "nums font-mono text-label",
                              toneClass(sp.sign),
                            )}
                          >
                            {sp.text}
                          </span>
                        </Link>
                      </li>
                    );
                  })}
                </ul>
                {filteredStocks.length > 200 ? (
                  <p className="mt-1 text-label text-fg-subtle">
                    +{formatCount(filteredStocks.length - 200)} more — narrow your search
                  </p>
                ) : null}
              </div>
            ) : null}
          </aside>
        ) : null}

        {/* Main content area */}
        <div className="flex min-h-0 flex-1 flex-col bg-[#000]">
          {/* Top bar: metric + legend */}
          <div className="flex shrink-0 items-center gap-3 border-b border-line px-3 py-1.5">
            <p data-testid="map-metric" className="shrink-0 text-label text-fg-muted">
              {metricLabelText}
            </p>
            <div data-testid="map-legend" className="flex min-w-0 flex-1 items-center gap-2">
              <LegendBar theme={theme} />
              <span className="shrink-0 font-mono text-[9px] text-fg-subtle">
                SIZE BY: {sizeBy === "cap" ? "MARKET CAP" : "EQUAL"}
              </span>
            </div>
            {ready && payload.asOf.length > 0 ? (
              <p className="ml-auto shrink-0 font-mono text-label text-fg-subtle">
                {clockLabel(payload.asOf)}
              </p>
            ) : null}
          </div>

          {/* View content */}
          <div className="relative flex min-h-0 flex-1">
            {mapView.status === "loading" ? (
              <Skeleton />
            ) : mapView.status === "error" ? (
              <ErrorState
                title="Market map failed to load"
                error={mapView.error ?? "Unknown error"}
                onRetry={mapView.reload}
                testId="map-error"
              />
            ) : payload === null || (payload.sectors.length === 0 && payload.mapped === 0) ? (
              <div
                data-testid="map-empty"
                className="flex min-h-0 flex-1 items-center justify-center"
              >
                <p className="text-data text-fg-muted">No map to draw</p>
              </div>
            ) : view === "treemap" ? (
              <MapCanvas
                payload={payload}
                theme={theme}
                onOpenSymbol={onOpenSymbol}
                onStats={onStats}
                className="relative min-h-0 flex-1"
              />
            ) : view === "matrix" ? (
              <MatrixView
                payload={payload}
                onOpenSymbol={onOpenSymbol}
                className="relative min-h-0 flex-1"
              />
            ) : view === "bubble" ? (
              <BubbleView
                payload={payload}
                onOpenSymbol={onOpenSymbol}
                className="relative min-h-0 flex-1"
              />
            ) : (
              <GridView
                payload={payload}
                onOpenSymbol={onOpenSymbol}
                className="relative min-h-0 flex-1"
              />
            )}
          </div>

          {/* Status footer */}
          {ready ? (
            <div className="flex shrink-0 items-center gap-4 border-t border-line px-3 py-1">
              <p data-testid="map-counts" className="font-mono text-label text-fg-subtle">
                {formatCount(payload.mapped)} / {formatCount(payload.universeTotal)} stocks ·{" "}
                {formatCount(totalSectors)} sectors
              </p>
              {stats !== null ? (
                <p data-testid="map-stats" className="font-mono text-label text-fg-subtle">
                  {formatCount(stats.drawn)} drawn
                  {stats.omitted > 0 ? ` · ${formatCount(stats.omitted)} too small` : ""}
                </p>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </main>
  );
}