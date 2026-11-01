/**
 * MiniMap: the market map at the size of a dashboard card.
 *
 * It is the same map, not a second one. `layoutMap` squarifies the payload,
 * `paintMap` paints it, `readTheme`/`prepareTheme` resolve the ramp — this file
 * adds only what an embed needs and a page does not:
 *
 *   * **No chrome.** No sidebar, no legend list, no hidden table. Ten sectors
 *     for a screen reader and a one-sentence label are the whole text surface.
 *   * **A popup that cannot eat the pointer.** `pointer-events: none`, fixed
 *     near the cursor, clamped to the viewport (it flips left/up at the edge
 *     rather than being cropped). Its links are decoration for a mouse — the
 *     canvas owns the click — but they keep a real href so the markup is not a
 *     dead end if the component is ever mounted without a handler.
 *   * **One canvas.** Tiles are arithmetic, not elements: `hitTest` answers
 *     the pointer against the same rects the painter used.
 *   * **Nothing is fetched.** The payload is a prop; the embed cannot start a
 *     request of its own, and a Home dashboard already has the document.
 *
 * The payload is the `/market/map` sectors array (`{id, value, symbols:[{s, c,
 * v}]}`), handed to the same reader the page uses — so absent values are
 * derived, duplicate tickers collapse, and a sector with no readable row is
 * dropped rather than drawn at zero. A parsed `MapSector` (the shape
 * `useMapData` returns) is accepted too: the two spellings carry the same
 * facts, and a silent empty box is the one failure this component must not
 * have.
 *
 * The counts a caller (and a test) can read are on the canvas:
 * `data-drawn`, `data-omitted`, `data-total`, `data-ms`, `data-dpr`,
 * `data-labels`, `data-symbols`, and `data-sectors` — the same rectangle wire
 * format `MapCanvas` publishes, so the geometry seam is shared.
 *
 * Nothing animates. The popup appears and disappears in one frame, so there is
 * no `prefers-reduced-motion` branch to get wrong.
 */

import * as React from "react";

import { appHref, navigate } from "@/shell/routes";

import { formatCount, marketCapLabel, signedPercent, type Sign } from "./format";
import { hitTest, layoutMap, type MapHit } from "./layout";
import { paintMap } from "./paint";
import { prepareTheme, readTheme } from "./theme";
import { parseMap, sectorCap, type MapPayload } from "./types";

/** The wire spelling: `s` symbol, `c` market cap USD billions, `v` percent. */
export interface MiniMapStockInput {
  readonly s: string;
  readonly c: number;
  readonly v?: number | null;
}

/** One sector as the endpoint sends it, with room for the page's own spelling. */
export interface MiniMapSectorInput {
  readonly id: string;
  readonly label?: string;
  readonly value?: number | null;
  readonly count?: number;
  readonly adv?: number;
  readonly dec?: number;
  readonly symbols?: readonly MiniMapStockInput[];
  /** A parsed `MapSector` (`useMapData`'s output) is understood as well. */
  readonly stocks?: readonly {
    readonly symbol: string;
    readonly cap: number;
    readonly change?: number;
  }[];
}

export interface MiniMapProps {
  /** The `/market/map` sectors array. Read once; never fetched here. */
  readonly sectors: readonly MiniMapSectorInput[];
  /** Opens a stock. Defaults to the `/stock/{symbol}` route. */
  readonly onOpenSymbol?: (symbol: string) => void;
  /** CSS pixels. The width comes from the container. */
  readonly height?: number;
  /** The map's name; the sector count and the largest block are always added. */
  readonly ariaLabel?: string;
}

const DEFAULT_HEIGHT = 220;
const POPUP_WIDTH = 208;
/** Fallback height for a box that cannot be measured (jsdom, a hidden pane). */
const POPUP_HEIGHT = 124;
/** The sectors named in the hidden list. Ten is a summary, not the table. */
const HIDDEN_ROWS = 10;

function hasNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** A sector in the wire shape `parseMap` reads, whichever spelling arrived. */
function wireSector(sector: MiniMapSectorInput): unknown {
  if (Array.isArray(sector.symbols)) return sector;
  const stocks = Array.isArray(sector.stocks) ? sector.stocks : [];
  return {
    ...sector,
    symbols: stocks.map((stock) => ({ s: stock.symbol, c: stock.cap, v: stock.change ?? 0 })),
  };
}

interface ReadSectors {
  readonly payload: MapPayload;
  /** Sectors whose source carried breadth, so the popup does not invent it. */
  readonly breadth: ReadonlySet<string>;
}

function readSectors(sectors: readonly MiniMapSectorInput[]): ReadSectors {
  const breadth = new Set<string>();
  const rows: unknown[] = [];
  for (const sector of sectors) {
    if (hasNumber(sector.adv) || hasNumber(sector.dec)) breadth.add(sector.id);
    rows.push(wireSector(sector));
  }
  return { payload: parseMap({ sectors: rows }), breadth };
}

/** The sector rectangle wire format, identical to `MapCanvas`'s. */
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

function toneClass(sign: Sign): string {
  if (sign === 1) return "text-up";
  if (sign === -1) return "text-down";
  if (sign === 0) return "text-flat";
  return "text-fg-subtle";
}

/**
 * Where the popup sits for a pointer at `x`, `y` in client coordinates.
 *
 * Twelve pixels right and below the cursor is the natural place; when that
 * overflows the viewport the box flips to the other side of the cursor rather
 * than sliding under it, and the margin clamp is the last resort for a viewport
 * narrower than the popup itself.
 *
 * The height is the popup's *measured* height, not a guess: four movers plus a
 * label and a breadth line is not a number this file can know once a reader's
 * font metrics change, and the first version of this function clipped 178 px of
 * popup into a 220 px viewport because it clamped against an estimate.
 */
function popupBox(x: number, y: number, width: number, height: number): { left: number; top: number } {
  const margin = 8;
  const offset = 12;
  const viewportW = window.innerWidth;
  const viewportH = window.innerHeight;

  let left = x + offset;
  if (left + width + margin > viewportW) left = x - width - offset;
  left = Math.max(margin, Math.min(left, viewportW - width - margin));

  let top = y + offset;
  if (top + height + margin > viewportH) top = y - height - offset;
  top = Math.max(margin, Math.min(top, viewportH - height - margin));

  return { left, top };
}

export function MiniMap({ sectors, onOpenSymbol, height = DEFAULT_HEIGHT, ariaLabel }: MiniMapProps) {
  const wrapRef = React.useRef<HTMLDivElement>(null);
  const canvasRef = React.useRef<HTMLCanvasElement>(null);

  const read = React.useMemo(() => readSectors(sectors), [sectors]);
  const payload = read.payload;
  const boxHeight = Math.max(0, Math.round(Number.isFinite(height) ? height : DEFAULT_HEIGHT));

  const [width, setWidth] = React.useState(0);
  const [hover, setHover] = React.useState<{ hit: MapHit; x: number; y: number } | null>(null);
  const popupRef = React.useRef<HTMLDivElement>(null);
  /** Offscreen until the popup's own box has been measured; never seen there. */
  const [placement, setPlacement] = React.useState({ left: -1, top: -1 });

  // The palette lives in the stylesheet on `:root`; one read at mount, then the
  // painter's prepared form. No effect: a second read would repaint the card
  // for a value that did not change.
  const palette = React.useMemo(() => prepareTheme(readTheme()), []);

  const layout = React.useMemo(() => layoutMap(payload, width, boxHeight), [payload, width, boxHeight]);
  const rects = React.useMemo(() => sectorRects(layout), [layout]);

  // Width only: the height is a prop, so the embed owns its own box and the
  // parent's flex row cannot make the card grow a pixel per frame.
  React.useEffect(() => {
    const node = wrapRef.current;
    if (!node) return;

    const apply = (value: number) => {
      const next = Math.max(0, Math.round(value));
      setWidth((previous) => (previous === next ? previous : next));
    };
    const measure = () => apply(node.getBoundingClientRect().width);

    measure();

    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", measure);
      return () => window.removeEventListener("resize", measure);
    }
    const observer = new ResizeObserver((entries) => {
      const entry = entries[entries.length - 1];
      if (entry) apply(entry.contentRect.width);
      else measure();
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  React.useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || width <= 0 || boxHeight <= 0) return;

    const dpr = Math.min(3, Math.max(1, window.devicePixelRatio || 1));
    const backingW = Math.round(width * dpr);
    const backingH = Math.round(boxHeight * dpr);
    if (canvas.width !== backingW) canvas.width = backingW;
    if (canvas.height !== backingH) canvas.height = backingH;

    const ctx = canvas.getContext("2d");
    if (!ctx) {
      canvas.dataset.ms = "0";
      return;
    }
    const started = performance.now();
    const stats = paintMap(ctx, layout, palette, { dpr, width, height: boxHeight });
    canvas.dataset.ms = (performance.now() - started).toFixed(2);
    canvas.dataset.dpr = String(dpr);
    canvas.dataset.painted = String(stats.tiles);
    canvas.dataset.labels = String(stats.labels);
    canvas.dataset.symbols = String(stats.symbols);
  }, [layout, palette, width, boxHeight]);

  // A new payload is a new map: a hover held over the old rects would name a
  // sector that is no longer under the pointer.
  React.useEffect(() => {
    setHover(null);
  }, [layout]);

  // Place the tooltip against its *measured* box, in the commit phase, so the
  // clamp is exact before the browser paints. A layout effect is what keeps the
  // one-frame correction invisible: the DOM is measured after mutation and the
  // new coordinates are committed in the same frame.
  React.useLayoutEffect(() => {
    const node = popupRef.current;
    if (!node || hover === null) return;
    const rect = node.getBoundingClientRect();
    const next = popupBox(
      hover.x,
      hover.y,
      rect.width > 0 ? rect.width : POPUP_WIDTH,
      rect.height > 0 ? rect.height : POPUP_HEIGHT,
    );
    setPlacement((previous) =>
      previous.left === next.left && previous.top === next.top ? previous : next,
    );
  }, [hover]);

  const hitAt = React.useCallback(
    (clientX: number, clientY: number): MapHit | null => {
      const canvas = canvasRef.current;
      if (!canvas) return null;
      const rect = canvas.getBoundingClientRect();
      return hitTest(layout, clientX - rect.left, clientY - rect.top);
    },
    [layout],
  );

  const open = React.useCallback(
    (symbol: string) => {
      if (onOpenSymbol) onOpenSymbol(symbol);
      else navigate(`/stock/${encodeURIComponent(symbol)}`);
    },
    [onOpenSymbol],
  );

  const total = payload.sectors.length;
  const largest = payload.sectors.reduce<(typeof payload.sectors)[number] | null>(
    (best, sector) => (best === null || sectorCap(sector) > sectorCap(best) ? sector : best),
    null,
  );
  // One sentence, one sector: the count and the block that dominates the
  // picture. A label that recited eleven (or twenty-one) names would be noise
  // between the reader and the list below it.
  const summary =
    total === 0 || largest === null
      ? "No sectors to draw"
      : `${formatCount(total)} ${total === 1 ? "sector" : "sectors"}, ` +
        `largest ${largest.label} ${signedPercent(largest.value).text}`;
  const label = `${ariaLabel ?? "Market map"}. ${summary}`;

  const hidden = React.useMemo(
    () =>
      [...payload.sectors]
        .sort((left, right) => sectorCap(right) - sectorCap(left))
        .slice(0, HIDDEN_ROWS),
    [payload],
  );

  if (total === 0) {
    // Not a blank canvas: a canvas with nothing on it is indistinguishable from
    // a broken one. The box says what is missing and keeps the same height, so
    // a dashboard row does not jump when data arrives.
    return (
      <div
        ref={wrapRef}
        data-testid="minimap"
        data-state="empty"
        style={{ height: boxHeight }}
        className="flex w-full items-center justify-center overflow-hidden rounded-sm border border-line bg-surface-sunken"
      >
        <div
          data-testid="minimap-empty"
          role="img"
          aria-label={label}
          className="flex h-full w-full items-center justify-center"
        >
          <p className="text-data text-fg-muted">No sectors to draw</p>
        </div>
      </div>
    );
  }

  return (
    <div
      ref={wrapRef}
      data-testid="minimap"
      data-state="ready"
      style={{ height: boxHeight }}
      className="relative w-full min-w-0 overflow-hidden rounded-sm bg-surface-sunken"
    >
      <canvas
        ref={canvasRef}
        data-testid="minimap-canvas"
        data-drawn={layout.drawn}
        data-omitted={layout.omitted}
        data-total={layout.total}
        data-sectors={rects}
        role="img"
        aria-label={label}
        className="block h-full w-full"
        onPointerMove={(event) => {
          const hit = hitAt(event.clientX, event.clientY);
          if (!hit) {
            setHover((current) => (current === null ? current : null));
            return;
          }
          setHover({ hit, x: event.clientX, y: event.clientY });
        }}
        onPointerLeave={() => setHover(null)}
        onClick={(event) => {
          const hit = hitAt(event.clientX, event.clientY);
          if (hit?.tile) open(hit.tile.symbol);
        }}
      />

      <ul data-testid="minimap-largest" aria-label="Largest sectors by market capitalisation" className="sr-only">
        {hidden.map((sector) => (
          <li key={sector.id}>
            {sector.label}: {marketCapLabel(sectorCap(sector))}, {signedPercent(sector.value).text}
          </li>
        ))}
      </ul>

      {hover ? (
        <div
          ref={popupRef}
          data-testid="minimap-popup"
          role="tooltip"
          aria-hidden="true"
          style={{
            position: "fixed",
            left: placement.left,
            top: placement.top,
            width: POPUP_WIDTH,
            pointerEvents: "none",
          }}
          className="pointer-events-none z-50 rounded-md border border-line-strong bg-surface-raised p-2 shadow-lg"
        >
          <div className="flex items-baseline justify-between gap-2">
            <p data-testid="minimap-popup-sector" className="min-w-0 truncate text-data font-semibold text-fg">
              {hover.hit.sector.label}
            </p>
            <p
              data-testid="minimap-popup-value"
              className={[
                "shrink-0 font-mono text-data nums",
                toneClass(signedPercent(hover.hit.sector.value).sign),
              ].join(" ")}
            >
              {signedPercent(hover.hit.sector.value).text}
            </p>
          </div>

          {read.breadth.has(hover.hit.sector.id) ? (
            <p
              data-testid="minimap-popup-breadth"
              className="mt-0.5 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle"
            >
              {formatCount(hover.hit.sector.adv)} adv · {formatCount(hover.hit.sector.dec)} dec
            </p>
          ) : null}

          <ul className="mt-1 space-y-0.5">
            {hover.hit.sector.top.slice(0, 4).map((stock) => {
              const change = signedPercent(stock.change);
              return (
                <li key={stock.symbol} className="flex items-center gap-1.5">
                  <a
                    href={appHref(`/stock/${encodeURIComponent(stock.symbol)}`)}
                    tabIndex={-1}
                    data-testid={`minimap-popup-stock-${stock.symbol}`}
                    onClick={(event) => {
                      if (!onOpenSymbol) return;
                      event.preventDefault();
                      onOpenSymbol(stock.symbol);
                    }}
                    className="min-w-0 flex-1 truncate font-mono text-data font-medium text-fg hover:text-accent-text"
                  >
                    {stock.symbol}
                  </a>
                  <span className={["nums font-mono text-data", toneClass(change.sign)].join(" ")}>
                    {change.text}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
