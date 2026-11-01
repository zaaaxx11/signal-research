/**
 * The canvas, its pointer, and its keyboard.
 *
 * One element draws the map; the document holds the popup, the announcement and
 * nothing per tile. Three things follow from that and are easy to break:
 *
 * **Hover is arithmetic.** `pointermove` is hit-tested against the same rects
 * the painter used (`hitTest`), not against the DOM. There is no element per
 * stock to be slow, to be mis-stacked, or to sit under the cursor and swallow
 * the next move.
 *
 * **The tooltip cannot eat the pointer.** It is `position: fixed` near the
 * cursor and `pointer-events: none`; if it were interactive, the popup would
 * steal the `pointermove` that is supposed to update it and the map would
 * freeze under its own tooltip.
 *
 * **DPR is a transform, not a CSS box.** The backing store is `css × dpr`, the
 * element stays CSS-sized, and the context is scaled once. Text is drawn at CSS
 * sizes into device pixels — sharp at DPR 2 instead of upscaled.
 *
 * The keyboard model: the canvas is focusable, arrows move a selection by
 * geometry (`nextTile`), Enter opens the stock, Escape clears. Every move is
 * announced through a `role="status"` paragraph, and the full data is also in
 * the page's visually hidden table.
 */

import * as React from "react";

import { LogoMark } from "@/grid/LogoMark";
import { Link, navigate } from "@/shell/routes";

import { formatCount, signedPercent } from "./format";
import { metricLabel } from "./format";
import { hitTest, indexOfSymbol, layoutMap, nextTile, type Direction, type MapHit, type Tile } from "./layout";
import { paintMap } from "./paint";
import { mergeTheme, prepareTheme, readTheme, type MapTheme } from "./theme";
import type { MapPayload } from "./types";

export interface MapCanvasStats {
  readonly drawn: number;
  readonly omitted: number;
}

export interface MapCanvasProps {
  readonly payload: MapPayload;
  readonly theme: MapTheme;
  /** Opens a stock. Defaults to the `/stock/{symbol}` route. */
  readonly onOpenSymbol?: (symbol: string) => void;
  /** Reports what was drawn and what was too small, for the page's footnote. */
  readonly onStats?: (stats: MapCanvasStats) => void;
  readonly className?: string;
}

const POPUP_WIDTH = 288;

function openStock(symbol: string, onOpen?: (symbol: string) => void): void {
  if (onOpen) onOpen(symbol);
  else navigate(`/stock/${encodeURIComponent(symbol)}`);
}

/** The sector rectangle wire format, small enough to sit in an attribute. */
function sectorRects(layout: ReturnType<typeof layoutMap>): string {
  return JSON.stringify(
    layout.sectors.map((sector) => ({
      id: sector.id,
      x: Math.round(sector.rect.x * 100) / 100,
      y: Math.round(sector.rect.y * 100) / 100,
      w: Math.round(sector.rect.w * 100) / 100,
      h: Math.round(sector.rect.h * 100) / 100,
    })),
  );
}

export function MapCanvas({ payload, theme, onOpenSymbol, onStats, className }: MapCanvasProps) {
  const wrapRef = React.useRef<HTMLDivElement>(null);
  const canvasRef = React.useRef<HTMLCanvasElement>(null);
  const statsRef = React.useRef(onStats);
  statsRef.current = onStats;

  const [size, setSize] = React.useState({ w: 0, h: 0 });
  const [hover, setHover] = React.useState<{ hit: MapHit; x: number; y: number } | null>(null);
  const [selected, setSelected] = React.useState<string | null>(null);
  // The palette lives in the stylesheet, so the live values are read after the
  // node exists (a future `[data-theme]` scope would be on or above it). The
  // props' values are the base: a slot the document cannot resolve keeps the
  // colour the caller passed rather than collapsing to transparent.
  const [resolvedTheme, setResolvedTheme] = React.useState<MapTheme>(theme);

  const layout = React.useMemo(
    () => layoutMap(payload, size.w, size.h),
    [payload, size.w, size.h],
  );
  const rects = React.useMemo(() => sectorRects(layout), [layout]);
  // Measure once and on every resize. A ResizeObserver entry is already
  // delivered at most once per frame, so applying it *is* the debounce; a drag
  // that changes the panel 60 times a second paints 60 times, which is what a
  // live resize should do. A run that lands on the same integer box is dropped.
  React.useEffect(() => {
    const node = wrapRef.current;
    if (!node) return;

    const apply = (width: number, height: number) => {
      const w = Math.max(0, Math.round(width));
      const h = Math.max(0, Math.round(height));
      setSize((previous) => (previous.w === w && previous.h === h ? previous : { w, h }));
    };
    const measure = () => {
      const rect = node.getBoundingClientRect();
      apply(rect.width, rect.height);
    };

    measure();

    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", measure);
      return () => window.removeEventListener("resize", measure);
    }
    const observer = new ResizeObserver((entries) => {
      const entry = entries[entries.length - 1];
      if (entry) apply(entry.contentRect.width, entry.contentRect.height);
      else measure();
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  // Read the tokens once the wrapper is in the document. `mergeTheme` keeps the
  // caller's value for any slot the document cannot resolve (jsdom, a failed
  // stylesheet), so the canvas is never painted with `undefined` colours.
  React.useEffect(() => {
    setResolvedTheme(() => mergeTheme(theme, readTheme(wrapRef.current)));
  }, [theme]);

  const activePalette = React.useMemo(() => prepareTheme(resolvedTheme), [resolvedTheme]);

  React.useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || size.w <= 0 || size.h <= 0) return;
    const dpr = Math.min(3, Math.max(1, window.devicePixelRatio || 1));
    // Assigning `width` reallocates and clears the backing store, so it happens
    // only when the box or the ratio actually changed — a selection repaint
    // must not pay for a new surface.
    const backingW = Math.round(size.w * dpr);
    const backingH = Math.round(size.h * dpr);
    if (canvas.width !== backingW) canvas.width = backingW;
    if (canvas.height !== backingH) canvas.height = backingH;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      canvas.dataset.drawMs = "0";
      return;
    }
    const started = performance.now();
    const drawn = paintMap(ctx, layout, activePalette, {
      dpr,
      width: size.w,
      height: size.h,
      selected,
    });
    const elapsed = performance.now() - started;
    canvas.dataset.drawMs = elapsed.toFixed(2);
    canvas.dataset.dpr = String(dpr);
    canvas.dataset.painted = String(drawn.tiles);
    canvas.dataset.drawn = String(layout.drawn);
    canvas.dataset.omitted = String(layout.omitted);
  }, [layout, activePalette, size, selected]);

  // Report the counts upward, once per layout — not once per paint. The parent
  // keeps them in state, so this must not depend on the parent's callback
  // identity or every parent render would repaint the canvas.
  const drawn = layout.drawn;
  const omitted = layout.omitted;
  React.useEffect(() => {
    // A zero-size canvas is not a map with nothing on it; reporting it would
    // flash "5,576 too small to draw" for the frame before the first measurement.
    if (size.w <= 0 || size.h <= 0) return;
    statsRef.current?.({ drawn, omitted });
  }, [drawn, omitted, size.w, size.h]);

  // A new map (a zoom, a reload) invalidates the old selection: a symbol that
  // is no longer on screen must not still be ringed or announced.
  React.useEffect(() => {
    setSelected((current) => (current && indexOfSymbol(layout, current) === -1 ? null : current));
    setHover(null);
  }, [layout]);

  const selectedTile = selected ? layout.tiles.find((tile) => tile.symbol === selected) ?? null : null;
  const selectedSector = selectedTile
    ? layout.sectors.find((sector) => sector.id === selectedTile.sectorId) ?? null
    : null;

  const announcement = selectedTile
    ? `${selectedTile.symbol}, ${selectedSector?.label ?? "sector"}, ${signedPercent(selectedTile.change).text}`
    : "";

  const hitAt = React.useCallback(
    (clientX: number, clientY: number): MapHit | null => {
      const canvas = canvasRef.current;
      if (!canvas) return null;
      const rect = canvas.getBoundingClientRect();
      return hitTest(layout, clientX - rect.left, clientY - rect.top);
    },
    [layout],
  );

  const move = React.useCallback(
    (direction: Direction) => {
      const from = selected ? indexOfSymbol(layout, selected) : -1;
      const index = nextTile(layout, from, direction);
      if (index < 0) return;
      const tile = layout.tiles[index];
      if (tile) setSelected(tile.symbol);
    },
    [layout, selected],
  );

  const onKeyDown = (event: React.KeyboardEvent<HTMLCanvasElement>) => {
    switch (event.key) {
      case "ArrowRight":
      case "ArrowLeft":
      case "ArrowUp":
      case "ArrowDown": {
        event.preventDefault();
        move(event.key === "ArrowRight" ? "right" : event.key === "ArrowLeft" ? "left" : event.key === "ArrowDown" ? "down" : "up");
        return;
      }
      case "Enter":
      case " ": {
        if (!selectedTile) return;
        event.preventDefault();
        openStock(selectedTile.symbol, onOpenSymbol);
        return;
      }
      case "Escape": {
        if (selected === null) return;
        event.preventDefault();
        setSelected(null);
        setHover(null);
        return;
      }
      default:
        return;
    }
  };

  const helpId = React.useId();
  const label = `Market map, ${metricLabel(payload.metric)}, ${formatCount(layout.drawn)} stocks in ${layout.sectors.length} sectors, size by market capitalisation.`;

  return (
    <div ref={wrapRef} data-testid="map-canvas-wrap" className={className ?? "relative min-h-0 flex-1"}>
      <canvas
        ref={canvasRef}
        data-testid="map-canvas"
        data-tiles={layout.drawn}
        data-tiles-total={layout.total}
        data-omitted={layout.omitted}
        data-sectors={rects}
        data-hover={hover ? `${hover.hit.sector.id}|${hover.hit.tile?.symbol ?? ""}` : ""}
        data-selected={selectedTile?.symbol ?? ""}
        tabIndex={0}
        role="application"
        aria-roledescription="market map"
        aria-label={label}
        aria-describedby={helpId}
        className="absolute inset-0 h-full w-full touch-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-focus"
        onPointerMove={(event) => {
          const hit = hitAt(event.clientX, event.clientY);
          if (!hit) {
            setHover((current) => (current === null ? current : null));
            return;
          }
          setHover({ hit, x: event.clientX, y: event.clientY });
        }}
        onPointerLeave={() => setHover(null)}
        onPointerDown={(event) => {
          // A click opens a stock; a drag is not a click, but pointerdown is
          // the only event a canvas gets before the browser starts a text
          // selection, so the hit test happens here and the click confirms it.
          const hit = hitAt(event.clientX, event.clientY);
          if (hit?.tile) setSelected(hit.tile.symbol);
        }}
        onClick={(event) => {
          const hit = hitAt(event.clientX, event.clientY);
          if (hit?.tile) openStock(hit.tile.symbol, onOpenSymbol);
        }}
        onFocus={() => {
          if (selected === null && layout.tiles.length > 0) {
            setSelected((layout.tiles[0] as Tile).symbol);
          }
        }}
        onKeyDown={onKeyDown}
      />

      <p id={helpId} className="sr-only">
        Treemap of the US market. Blocks are sectors, sized by market cap and coloured by
        performance. Use the arrow keys to move between stocks, Enter to open the selected
        stock, Escape to clear the selection. The same data is listed as a table below.
      </p>

      <p data-testid="map-announcer" role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>

      {hover ? (
        <div
          data-testid="map-popup"
          role="tooltip"
          aria-hidden="true"
          style={{
            position: "fixed",
            left: Math.min(hover.x + 14, Math.max(8, window.innerWidth - POPUP_WIDTH - 12)),
            top: Math.min(hover.y + 14, Math.max(8, window.innerHeight - 268)),
            width: POPUP_WIDTH,
            pointerEvents: "none",
          }}
          className="pointer-events-none z-50 rounded-md border border-line-strong bg-surface-raised p-2 shadow-lg"
        >
          <div className="flex items-baseline justify-between gap-2">
            <p data-testid="map-popup-sector" className="min-w-0 truncate text-data font-semibold text-fg">
              {hover.hit.sector.label}
            </p>
            <p
              data-testid="map-popup-value"
              className={[
                "shrink-0 font-mono text-data nums",
                signedPercent(hover.hit.sector.value).sign === 1
                  ? "text-up"
                  : signedPercent(hover.hit.sector.value).sign === -1
                    ? "text-down"
                    : "text-flat",
              ].join(" ")}
            >
              {signedPercent(hover.hit.sector.value).text}
            </p>
          </div>

          <p className="mt-0.5 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
            {formatCount(hover.hit.sector.adv)} adv · {formatCount(hover.hit.sector.dec)} dec ·{" "}
            {formatCount(hover.hit.sector.count)} stocks
          </p>

          <ul className="mt-1.5 space-y-0.5">
            {hover.hit.sector.top.map((stock) => {
              const change = signedPercent(stock.change);
              return (
                <li key={stock.symbol} className="flex items-center gap-1.5">
                  <LogoMark symbol={stock.symbol} size={14} />
                  <Link
                    to={`/stock/${encodeURIComponent(stock.symbol)}`}
                    tabIndex={-1}
                    data-testid={`map-popup-stock-${stock.symbol}`}
                    className="min-w-0 flex-1 truncate font-mono text-data font-medium text-fg hover:text-accent-text"
                  >
                    {stock.symbol}
                  </Link>
                  <span
                    className={[
                      "nums font-mono text-data",
                      change.sign === 1 ? "text-up" : change.sign === -1 ? "text-down" : "text-flat",
                    ].join(" ")}
                  >
                    {change.text}
                  </span>
                </li>
              );
            })}
          </ul>

          <p className="mt-1.5 border-t border-line pt-1 font-mono text-label text-fg-subtle">
            Size: market cap · Colour: {metricLabel(payload.metric).toLowerCase()}
          </p>
        </div>
      ) : null}
    </div>
  );
}
