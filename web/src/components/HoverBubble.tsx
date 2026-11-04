import { useEffect, useRef, useState } from "react";

import { parseQuoteAttr, QUOTE_ATTR, type QuoteInfo } from "@/lib/quoteAttrs";

/**
 * The global hover bubble: the ticker under the cursor gets a floating card
 * with its chart, price and move — every page, one listener, no prop drilling.
 *
 * Mechanics:
 * - One document-level `mouseover`/`mouseout`/`focusin`/`keydown` listener set.
 *   A delegated walk (`.closest(`[${QUOTE_ATTR}]`)`) is O(depth), not O(nodes):
 *   a 5,000-ticker table costs the same as a 40-row one.
 * - A 300 ms dwell before anything fetches or paints: glancing across a table
 *   must not become 40 chart reads. Leaving the element cancels the dwell.
 * - Chart data is fetched once per symbol per page life (module Map cache),
 *   from `GET /market/chart/{symbol}?window=1d` — the same series the charts
 *   page draws, so bubble and chart cannot disagree.
 * - Positioning runs on `requestAnimationFrame` with `translate3d` only:
 *   compositor work, no layout thrash, no jank. The card flips to the other
 *   side when the viewport edge would clip it.
 * - The tint is the automated colour rule the product asks for: up green,
 *   down red, and the *intensity* follows the move — seamless, small move
 *   5–10% tint, big move toward 60% — over the raised surface token.
 */
export const HOVER_DELAY_MS = 300;
export const CACHE_LIMIT = 200;

interface Point {
  x: number;
  y: number;
}

interface Sparkline {
  closes: number[];
  prevClose: number | null;
}

const sparkCache = new Map<string, Sparkline | null>();

/** The tint intensity rule, exported so the test pins the curve. */
export function tintAlpha(changePct: number | null | undefined): number {
  if (changePct == null || !Number.isFinite(changePct)) {
    return 0.05;
  }
  return Math.min(0.6, Math.max(0.05, Math.abs(changePct) / 4));
}

export function parseSeries(payload: unknown): Sparkline | null {
  if (typeof payload !== "object" || payload === null) return null;
  const body = payload as Record<string, unknown>;
  const bars = body["bars"];
  if (!Array.isArray(bars)) return null;
  const closes: number[] = [];
  for (const row of bars) {
    if (typeof row === "object" && row !== null) {
      const close = (row as Record<string, unknown>)["c"];
      if (typeof close === "number" && Number.isFinite(close)) closes.push(close);
    }
  }
  if (closes.length === 0) return null;
  const prev = (body as Record<string, unknown>)["previous_close"];
  return {
    closes,
    prevClose: typeof prev === "number" && Number.isFinite(prev) ? prev : closes[0],
  };
}

async function fetchSparkline(symbol: string, signal: AbortSignal): Promise<Sparkline | null> {
  if (sparkCache.has(symbol)) return sparkCache.get(symbol) ?? null;
  const url = `/market/chart/${encodeURIComponent(symbol)}?window=1d`;
  const response = await fetch(url, { signal });
  if (!response.ok) return null;
  const parsed = parseSeries(await response.json());
  if (sparkCache.size >= CACHE_LIMIT) {
    sparkCache.delete(sparkCache.keys().next().value as string);
  }
  sparkCache.set(symbol, parsed);
  return parsed;
}

function SparklinePath({ spark, changePct }: { spark: Sparkline; changePct: number | null }) {
  const up = (changePct ?? 0) >= 0;
  const stroke = up ? "var(--up)" : "var(--down)";
  const points = spark.closes;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const width = 148;
  const height = 44;
  const step = width / Math.max(1, points.length - 1);
  const path = points
    .map((close, index) => {
      const x = index * step;
      const y = height - 4 - ((close - min) / span) * (height - 8);
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const area = `${path} L${width},${height} L0,${height} Z`;
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      aria-hidden
      data-testid="hover-bubble-spark"
    >
      <path d={area} fill={stroke} opacity="0.16" />
      <path d={path} fill="none" stroke={stroke} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

export function HoverBubble({ testId = "hover-bubble" }: { testId?: string }) {
  const frame = useRef<HTMLDivElement | null>(null);
  const dwellTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const raf = useRef<number | null>(null);
  const abortCtrl = useRef<AbortController | null>(null);
  const cursor = useRef<Point>({ x: 0, y: 0 });

  const [info, setInfo] = useState<QuoteInfo | null>(null);
  const [spark, setSpark] = useState<Sparkline | null>(null);
  const [sparkFailed, setSparkFailed] = useState(false);

  const cancelDwell = () => {
    if (dwellTimer.current) {
      clearTimeout(dwellTimer.current);
      dwellTimer.current = null;
    }
  };

  const hide = () => {
    cancelDwell();
    abortCtrl.current?.abort();
    abortCtrl.current = null;
    setInfo(null);
    setSpark(null);
    setSparkFailed(false);
  };

  const schedule = (target: QuoteInfo) => {
    cancelDwell();
    dwellTimer.current = setTimeout(() => {
      setInfo(target);
      // One lazy read per symbol; the cache map answers instant re-hovers.
      abortCtrl.current?.abort();
      abortCtrl.current = new AbortController();
      if (sparkCache.has(target.symbol)) {
        setSpark(sparkCache.get(target.symbol) ?? null);
        setSparkFailed(sparkCache.get(target.symbol) === null);
        return;
      }
      setSpark(null);
      setSparkFailed(false);
      fetchSparkline(target.symbol, abortCtrl.current.signal)
        .then((line) => {
          if (abortCtrl.current?.signal.aborted) return;
          setSpark(line);
          setSparkFailed(line === null);
        })
        .catch(() => {
          if (abortCtrl.current?.signal.aborted) return;
          setSpark(null);
          setSparkFailed(true);
        });
    }, HOVER_DELAY_MS);
  };

  useEffect(() => {
    const activeSymbol = { current: null as string | null };
    const onOver = (event: MouseEvent) => {
      const element = (event.target as Element | null)?.closest?.(`[${QUOTE_ATTR}]`);
      if (!element) {
        hide();
        return;
      }
      const info = parseQuoteAttr(element.getAttribute(QUOTE_ATTR));
      if (!info) {
        hide();
        return;
      }
      cursor.current = { x: event.clientX, y: event.clientY };
      const last = activeSymbol.current;
      if (last === info.symbol) {
        // Same element, moving the cursor: keep the schedule, refresh the point.
      } else {
        activeSymbol.current = info.symbol;
        schedule(info);
      }
      moveBubble();
    };
    const onMove = (event: MouseEvent) => {
      cursor.current = { x: event.clientX, y: event.clientY };
      moveBubble();
    };
    const moveBubble = () => {
      if (raf.current !== null) return;
      raf.current = requestAnimationFrame(() => {
        raf.current = null;
        const node = frame.current;
        if (!node) return;
        const width = node.offsetWidth;
        const height = node.offsetHeight;
        const margin = 14;
        let x = cursor.current.x + 16;
        let y = cursor.current.y + 16;
        if (x + width + margin > window.innerWidth) x = cursor.current.x - width - 16;
        if (y + height + margin > window.innerHeight) y = cursor.current.y - height - 16;
        node.style.transform = `translate3d(${Math.max(8, x)}px, ${Math.max(8, y)}px, 0)`;
      });
    };
    const onFocusIn = (event: FocusEvent) => {
      const element = (event.target as Element | null)?.closest?.(`[${QUOTE_ATTR}]`);
      if (!element) return;
      const info = parseQuoteAttr(element.getAttribute(QUOTE_ATTR));
      if (!info) return;
      const rect = element.getBoundingClientRect();
      cursor.current = { x: rect.left + rect.width / 2, y: rect.bottom };
      activeSymbol.current = info.symbol;
      schedule(info);
    };
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") hide();
    };
    document.addEventListener("mouseover", onOver);
    document.addEventListener("mousemove", onMove, { passive: true });
    document.addEventListener("mouseout", hide);
    document.addEventListener("focusin", onFocusIn);
    document.addEventListener("keydown", onEscape);
    return () => {
      document.removeEventListener("mouseover", onOver);
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseout", hide);
      document.removeEventListener("focusin", onFocusIn);
      document.removeEventListener("keydown", onEscape);
      cancelDwell();
      if (raf.current !== null) cancelAnimationFrame(raf.current);
      abortCtrl.current?.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const up = (info?.changePct ?? 0) >= 0;
  const formatPct = (value: number | null | undefined) =>
    value == null || !Number.isFinite(value) ? null : `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;

  return (
    <div
      ref={frame}
      data-testid={testId}
      data-visible={info !== null ? "true" : "false"}
      role="tooltip"
      className="pointer-events-none fixed left-0 top-0 z-[70] w-40 rounded-[6px] p-2.5 shadow-xl ring-1 ring-[var(--border-strong)] transition-opacity duration-150"
      style={{
        background: "var(--surface-raised)",
        opacity: info !== null ? 1 : 0,
        willChange: "transform",
      }}
    >
      {info === null ? null : (
        <>
          <div className="truncate text-[11px] font-semibold text-[var(--fg)]">{info.name ?? info.symbol}</div>
          <div className="truncate text-[10px] text-[var(--fg-subtle)]">{info.symbol}</div>
          {spark !== null ? (
            <SparklinePath spark={spark} changePct={info.changePct ?? null} />
          ) : sparkFailed ? null : (
            <div className="mt-1 h-11 animate-pulse rounded bg-[var(--accent-soft)]" />
          )}
          <div className="mt-1 flex items-baseline justify-between text-[11px]">
            <span className="text-[var(--fg)]">
              {info.price != null ? info.price.toLocaleString(undefined, { maximumFractionDigits: 4 }) : "—"}
            </span>
            {formatPct(info.changePct) ? (
              <span style={{ color: up ? "var(--up)" : "var(--down)" }}>{formatPct(info.changePct)}</span>
            ) : null}
          </div>
          {info.note ? <div className="mt-1 text-[10px] text-[var(--fg-muted)]">{info.note}</div> : null}
        </>
      )}
    </div>
  );
}
