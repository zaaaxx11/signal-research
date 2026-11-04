/**
 * Maps: the whole US market as one canvas, and the same facts as text.
 *
 * The page is a frame around `MapCanvas`: a strip that says what is being drawn
 * and when, a sidebar that names the metric and lets a sector be isolated, and
 * a visually hidden table that carries every sector for a screen reader, a
 * text-mode browser, or anyone who would rather not read a picture. The canvas
 * is the only thing that needs JavaScript to be *understood*; the table is the
 * page's floor.
 *
 * Zoom is a filter, not a second layout engine: clicking a legend row narrows
 * the payload to one sector, and the canvas lays that one sector out into the
 * whole box. Clicking it again (or "All sectors") restores the market. That is
 * why the layout is a pure function of the payload and the box — every state
 * the reader can reach is the same code path.
 *
 * Colours come from `theme.ts`, which reads the tokens; the legend swatches and
 * the tiles are therefore painted by the same ramp, at the same saturation, and
 * a sector's swatch cannot disagree with its block.
 */

import * as React from "react";

import { cn } from "@/lib/utils";

import { clockLabel, formatCount, marketCapLabel, signedPercent, toneClass } from "./format";
import { MapCanvas, type MapCanvasStats } from "./MapCanvas";
import { readTheme, swatch } from "./theme";
import { sectorCap } from "./types";
import { useMapData } from "./useMapData";

const TIMEFRAMES = [
  { id: "1d", label: "1D" },
  { id: "1w", label: "1W" },
  { id: "1m", label: "1M" },
] as const;

const TIMEFRAME_LABELS: Record<string, string> = {
  "1d": "1-Day performance",
  "1w": "1-Week performance",
  "1m": "1-Month performance",
};

const MAP_TYPES = [
  { id: "us", label: "US Stocks" },
  { id: "us_etf", label: "ETFs" },
  { id: "global", label: "World" },
] as const;

export interface MapPageProps {
  /** Defaults to the US market, the only region the endpoint serves today. */
  readonly region?: string;
  /** Opens a stock. Defaults to the `/stock/{symbol}` route. */
  readonly onOpenSymbol?: (symbol: string) => void;
}

function Retry({ onRetry }: { readonly onRetry: () => void }) {
  return (
    <button
      type="button"
      onClick={onRetry}
      className="rounded-sm border border-line-control px-3 py-1 text-ui text-fg transition-colors hover:bg-row-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus"
    >
      Retry
    </button>
  );
}

export function MapPage({ region = "us", onOpenSymbol }: MapPageProps) {
  const [timeframe, setTimeframe] = React.useState("1d");
  const [mapType, setMapType] = React.useState(region);
  const view = useMapData({ region: mapType, timeframe });
  const theme = React.useMemo(() => readTheme(), []);
  const [focus, setFocus] = React.useState<string | null>(null);
  const [stats, setStats] = React.useState<MapCanvasStats | null>(null);

  // Stable identity: the canvas keeps this in a ref and calls it once per
  // layout, but a new function per render would still be noise in the props.
  const onStats = React.useCallback((next: MapCanvasStats) => {
    setStats((previous) =>
      previous !== null && previous.drawn === next.drawn && previous.omitted === next.omitted
        ? previous
        : next,
    );
  }, []);

  const payload = view.payload;

  // The label follows the reader's own choice, not the wire: the payload's
  // `metric` is the API's name for the same window, and it only exists once
  // the document lands.
  const metricLabelText = TIMEFRAME_LABELS[timeframe] ?? "Performance";

  // Largest sector first — the layout's order, the legend's order and the
  // keyboard's order are one decision. Tie-break by id so it is stable.
  const sectors = React.useMemo(() => {
    if (payload === null) return [];
    return [...payload.sectors].sort((left, right) => {
      const cap = sectorCap(right) - sectorCap(left);
      if (cap !== 0) return cap;
      return left.id < right.id ? -1 : left.id > right.id ? 1 : 0;
    });
  }, [payload]);

  // A focus on a sector the new payload does not contain falls back to the
  // whole market rather than to an empty canvas.
  const focused = React.useMemo(() => {
    if (payload === null) return null;
    if (focus === null) return payload;
    const only = payload.sectors.filter((sector) => sector.id === focus);
    return only.length === 0 ? payload : { ...payload, sectors: only };
  }, [payload, focus]);

  const ready = view.status === "ready" && payload !== null;
  const empty = ready && (payload.sectors.length === 0 || payload.mapped === 0);
  const focusedLabel = focus === null ? null : sectors.find((sector) => sector.id === focus)?.label ?? null;

  return (
    <main
      data-testid="map-page"
      data-state={view.status}
      className="@container flex min-h-0 flex-1 flex-col overflow-hidden bg-bg"
    >
      <header className="flex h-8 shrink-0 items-center gap-2 border-b border-line bg-surface px-2">
        <h1 className="shrink-0 text-ui font-semibold text-fg">Maps</h1>
        <div
          data-testid="map-types"
          role="group"
          aria-label="Map type"
          className="flex shrink-0 items-center gap-px"
        >
          {MAP_TYPES.map((option) => {
            const active = option.id === mapType;
            return (
              <button
                key={option.id}
                type="button"
                data-testid={`map-type-${option.id}`}
                aria-pressed={active}
                onClick={() => setMapType(option.id)}
                className={cn(
                  "h-6 rounded-sm border px-2 font-mono text-label uppercase tracking-[0.08em] transition-colors",
                  active
                    ? "border-brand/60 bg-row-selected text-fg"
                    : "border-transparent text-fg-muted hover:text-fg",
                )}
              >
                {option.label}
              </button>
            );
          })}
        </div>
        <div
          data-testid="map-timeframes"
          role="group"
          aria-label="Map timeframe"
          className="flex shrink-0 items-center gap-px"
        >
          {TIMEFRAMES.map((option) => {
            const active = option.id === timeframe;
            return (
              <button
                key={option.id}
                type="button"
                data-testid={`map-tf-${option.id}`}
                aria-pressed={active}
                onClick={() => setTimeframe(option.id)}
                className={cn(
                  "rounded-sm px-1.5 py-0.5 font-mono text-label uppercase tracking-[0.08em] transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus",
                  active
                    ? "bg-row-selected text-accent-text"
                    : "text-fg-subtle hover:bg-row-hover hover:text-fg",
                )}
              >
                {option.label}
              </button>
            );
          })}
        </div>
        <p data-testid="map-metric" className="shrink-0 text-data text-fg-muted">
          {metricLabelText}
        </p>
        {ready ? (
          <p data-testid="map-counts" className="min-w-0 truncate font-mono text-label uppercase tracking-[0.06em] text-fg-subtle">
            {formatCount(payload.mapped)} / {formatCount(payload.universeTotal)} stocks ·{" "}
            {formatCount(sectors.length)} sectors · size by market cap
          </p>
        ) : null}
        {ready && payload.asOf.length > 0 ? (
          <p className="ml-auto shrink-0 font-mono text-label text-fg-subtle">{clockLabel(payload.asOf)}</p>
        ) : null}
      </header>

      <div className="flex min-h-0 flex-1 flex-col @3xl:flex-row">
        <section
          aria-label="Market map"
          data-testid="map-panel"
          className="relative flex min-h-[240px] flex-1 flex-col overflow-hidden bg-surface-sunken"
        >
          {view.status === "loading" ? (
            <div
              data-testid="map-loading"
              aria-busy="true"
              aria-label="Loading the market map"
              className="flex min-h-0 flex-1 items-center justify-center p-4"
            >
              <div className="w-full max-w-[520px]">
                <div className="h-3 w-2/3 animate-pulse rounded-sm bg-accent-soft" />
                <div className="mt-2 h-3 w-1/2 animate-pulse rounded-sm bg-accent-soft" />
                <p className="mt-3 text-data text-fg-subtle">Reading the market map…</p>
              </div>
            </div>
          ) : view.status === "error" ? (
            <div
              role="alert"
              data-testid="map-error"
              className="m-2 flex items-start justify-between gap-3 rounded-md border border-line border-l-2 border-l-alert bg-surface px-3 py-2"
            >
              <div className="min-w-0">
                <p className="text-data text-fg">The market map did not load.</p>
                <p data-testid="map-error-message" className="mt-0.5 text-data text-fg-muted">
                  {view.error ?? "Unknown error"}
                </p>
              </div>
              <Retry onRetry={view.reload} />
            </div>
          ) : empty ? (
            <div data-testid="map-empty" className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 p-4 text-center">
              <p className="text-ui text-fg">No map to draw</p>
              <p className="max-w-[420px] text-data text-fg-muted">
                The scan answered with no sector rows carrying a market cap, so there is nothing to size.
              </p>
              <Retry onRetry={view.reload} />
            </div>
          ) : focused !== null ? (
            <MapCanvas
              payload={focused}
              theme={theme}
              onOpenSymbol={onOpenSymbol}
              onStats={onStats}
              className="relative min-h-0 flex-1"
            />
          ) : null}
        </section>

        <aside
          data-testid="map-sidebar"
          className="flex w-full shrink-0 flex-col gap-3 overflow-y-auto border-t border-line bg-surface px-2 py-2 @3xl:w-[236px] @3xl:border-l @3xl:border-t-0"
        >
          <div>
            <p className="text-label uppercase tracking-[0.1em] text-fg-subtle">Metric</p>
            <p data-testid="map-metric-label" className="text-ui text-fg">
              {metricLabelText}
            </p>
            <p className="mt-0.5 text-label text-fg-subtle">Size by Market Cap</p>
          </div>

          <div className="min-h-0">
            <div className="flex items-baseline justify-between gap-2">
              <h2 className="text-label uppercase tracking-[0.1em] text-fg-subtle">Sectors</h2>
              {focusedLabel !== null ? (
                <button
                  type="button"
                  data-testid="map-legend-all"
                  onClick={() => setFocus(null)}
                  className="rounded-sm px-1 text-label text-accent-text hover:text-fg"
                >
                  All sectors
                </button>
              ) : null}
            </div>

            <ul data-testid="map-legend" className="mt-1 space-y-px">
              {sectors.map((sector) => {
                const signed = signedPercent(sector.value);
                const active = focus === sector.id;
                return (
                  <li key={sector.id} className="min-w-0">
                    <button
                      type="button"
                      aria-pressed={active}
                      data-testid={`map-legend-${sector.id}`}
                      title={`${sector.label}: ${marketCapLabel(sectorCap(sector))} · ${signed.text} · ${formatCount(sector.count)} stocks`}
                      onClick={() => setFocus((current) => (current === sector.id ? null : sector.id))}
                      className={cn(
                        "flex w-full items-center gap-1.5 rounded-sm px-1 py-0.5 text-left transition-colors hover:bg-row-hover",
                        active && "bg-row-selected",
                      )}
                    >
                      <span
                        aria-hidden="true"
                        data-testid={`map-legend-swatch-${sector.id}`}
                        className="size-2 shrink-0 rounded-[1px] border border-line"
                        style={{ backgroundColor: swatch(sector.value, theme) }}
                      />
                      <span className="min-w-0 flex-1 truncate text-data text-fg">{sector.label}</span>
                      <span className={cn("nums shrink-0 font-mono text-label", toneClass(signed.sign))}>
                        {signed.text}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>

          <div className="mt-auto space-y-1 border-t border-line pt-2">
            {focusedLabel !== null ? (
              <p data-testid="map-focus-note" className="text-label text-fg-muted">
                Showing {focusedLabel} only.
              </p>
            ) : null}
            <p className="text-label text-fg-subtle">
              Hover a block for its stocks · arrow keys move · Enter opens · Esc clears
            </p>
            {ready && stats !== null ? (
              <p data-testid="map-stats" className="font-mono text-label text-fg-subtle">
                {formatCount(stats.drawn)} drawn
                {stats.omitted > 0 ? ` · ${formatCount(stats.omitted)} too small to draw` : ""}
              </p>
            ) : null}
          </div>
        </aside>
      </div>

      {ready ? (
        // The wrapper is the `sr-only` box, not the table: a `<table>` is
        // `display: table` and ignores `width: 1px`, so hiding the table itself
        // leaves a 700 px absolute box on the page (clipped, but not a 1 px
        // element). A block wrapper is exactly 1×1 and clips its child.
        <div data-testid="map-table-wrap" className="sr-only">
          <table data-testid="map-table">
            <caption>
              Market map by sector. {metricLabelText}; blocks are sized by market cap.{" "}
              {formatCount(payload.mapped)} of {formatCount(payload.universeTotal)} stocks mapped.
            </caption>
            <thead>
              <tr>
                <th scope="col">Sector</th>
                <th scope="col">Stocks</th>
                <th scope="col">Advancing</th>
                <th scope="col">Declining</th>
                <th scope="col">Average change</th>
                <th scope="col">Market cap</th>
              </tr>
            </thead>
            <tbody>
              {sectors.map((sector) => (
                <tr key={sector.id} data-testid={`map-row-${sector.id}`}>
                  <th scope="row">{sector.label}</th>
                  <td>{formatCount(sector.count)}</td>
                  <td>{formatCount(sector.adv)}</td>
                  <td>{formatCount(sector.dec)}</td>
                  <td>{signedPercent(sector.value).text}</td>
                  <td>{marketCapLabel(sectorCap(sector))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </main>
  );
}
