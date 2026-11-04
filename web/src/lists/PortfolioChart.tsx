/**
 * Allocation chart: donut + bar.
 *
 * Drawn from priced holdings — an unpriced holding has no market value, so it
 * cannot appear in an allocation breakdown. The section renders only when there
 * are at least two priced holdings; one slice is not an allocation.
 */

import * as React from "react";
import { formatMoney } from "@/grid/format";
import type { HoldingPnl } from "./pnl";

/** A palette of 10 colours, spaced evenly around the hue circle. */
const SLICE_RGB = [
  "rgb(232,82,143)",   // rose (--accent-strong)
  "rgb(61,220,151)",   // green (--up)
  "rgb(255,180,84)",   // amber (--alert)
  "rgb(130,150,255)",  // blue
  "rgb(200,130,255)",  // purple
  "rgb(255,140,100)",  // coral
  "rgb(80,200,220)",   // teal
  "rgb(220,180,60)",   // gold
  "rgb(160,200,100)",  // lime
  "rgb(255,120,180)",  // pink
];

interface Slice {
  readonly symbol: string;
  readonly marketValue: number;
  readonly pct: number;       // 0–100
  readonly startAngle: number; // radians
  readonly endAngle: number;   // radians
  readonly gainPct: number | null;
}

interface AllocationChartProps {
  readonly rows: readonly HoldingPnl[];
}

/** Build slice geometry from priced rows, sorted largest first. */
function buildSlices(rows: readonly HoldingPnl[]): Slice[] {
  const priced = rows.filter((r) => r.marketValue !== null);
  if (priced.length < 2) return [];

  // Sort largest first so the biggest slice starts at the top.
  const sorted = [...priced].sort((a, b) => (b.marketValue ?? 0) - (a.marketValue ?? 0));
  const total = sorted.reduce((sum, r) => sum + (r.marketValue ?? 0), 0);
  if (total <= 0) return [];

  let angle = -Math.PI / 2; // start at 12 o'clock
  const slices: Slice[] = [];

  for (const row of sorted) {
    const mv = row.marketValue!;
    const pct = (mv / total) * 100;
    const sweep = (pct / 100) * 2 * Math.PI;
    slices.push({
      symbol: row.holding.symbol,
      marketValue: mv,
      pct,
      startAngle: angle,
      endAngle: angle + sweep,
      gainPct: row.pnlPct,
    });
    angle += sweep;
  }

  return slices;
}

function arcPath(
  cx: number,
  cy: number,
  outerR: number,
  innerR: number,
  startAngle: number,
  endAngle: number,
): string {
  const x1 = cx + outerR * Math.cos(startAngle);
  const y1 = cy + outerR * Math.sin(startAngle);
  const x2 = cx + outerR * Math.cos(endAngle);
  const y2 = cy + outerR * Math.sin(endAngle);
  const x3 = cx + innerR * Math.cos(endAngle);
  const y3 = cy + innerR * Math.sin(endAngle);
  const x4 = cx + innerR * Math.cos(startAngle);
  const y4 = cy + innerR * Math.sin(startAngle);

  const large = endAngle - startAngle > Math.PI ? 1 : 0;

  return [
    `M ${x1} ${y1}`,
    `A ${outerR} ${outerR} 0 ${large} 1 ${x2} ${y2}`,
    `L ${x3} ${y3}`,
    `A ${innerR} ${innerR} 0 ${large} 0 ${x4} ${y4}`,
    "Z",
  ].join(" ");
}

function barColour(gainPct: number | null): string {
  if (gainPct === null) return "var(--flat)";
  if (gainPct > 0) return "var(--up)";
  if (gainPct < 0) return "var(--down)";
  return "var(--flat)";
}

export function AllocationChart({ rows }: AllocationChartProps) {
  const slices = React.useMemo(() => buildSlices(rows), [rows]);
  if (slices.length < 2) return null;

  const totalValue = slices.reduce((sum, s) => sum + s.marketValue, 0);
  const totalLabel = formatMoney(totalValue);

  const donutOuter = 80;
  const donutInner = 50;
  const chartSize = 180;
  const cx = chartSize / 2;
  const cy = chartSize / 2;

  // Top 5 by value for the bar chart.
  const top5 = slices.slice(0, 5);

  return (
    <section data-testid="portfolio-allocation" className="mt-2 rounded-md border border-line bg-surface px-3 py-2.5">
      <h2 className="text-label font-medium uppercase tracking-[0.06em] text-fg-muted mb-2">
        Allocation
      </h2>

      <div className="flex flex-wrap items-start gap-4">
        {/* Donut */}
        <div className="relative shrink-0" style={{ width: chartSize, height: chartSize }}>
          <svg
            viewBox={`0 0 ${chartSize} ${chartSize}`}
            width={chartSize}
            height={chartSize}
            aria-label={`Portfolio allocation: ${slices.length} holdings, total ${totalLabel}`}
            role="img"
          >
            {slices.map((s, i) => {
              const rgb = SLICE_RGB[i % SLICE_RGB.length];
              const titleText = `${s.symbol}: ${formatMoney(s.marketValue)} (${s.pct.toFixed(1)}%)`;
              return (
                <path
                  key={s.symbol}
                  d={arcPath(cx, cy, donutOuter, donutInner, s.startAngle, s.endAngle)}
                  fill={rgb}
                  stroke="var(--surface)"
                  strokeWidth={1}
                  data-testid={`donut-slice-${s.symbol}`}
                >
                  <title>{titleText}</title>
                </path>
              );
            })}
          </svg>
          {/* Center total */}
          <div
            className="absolute inset-0 flex flex-col items-center justify-center"
            aria-hidden="true"
          >
            <span className="font-mono text-[0.625rem] leading-tight text-fg-muted">
              Total
            </span>
            <span className="font-mono text-data font-semibold text-fg leading-tight nums">
              {totalLabel}
            </span>
          </div>
        </div>

        {/* Legend / top holdings */}
        <div className="min-w-0 flex-1" style={{ maxWidth: 260 }}>
          <h3 className="text-label uppercase tracking-[0.05em] text-fg-subtle mb-1">
            Top holdings
          </h3>
          <ul className="space-y-0.5" data-testid="allocation-bars">
            {top5.map((s, i) => (
              <li key={s.symbol} className="flex flex-col" data-testid={`allocation-bar-${s.symbol}`}>
                <div className="flex items-center justify-between gap-2">
                  <span className="flex items-center gap-1.5 min-w-0">
                    <span
                      className="inline-block h-2 w-2 shrink-0 rounded-[1px]"
                      style={{ backgroundColor: SLICE_RGB[i % SLICE_RGB.length] }}
                      aria-hidden="true"
                    />
                    <span className="font-mono text-data font-semibold text-fg">
                      {s.symbol}
                    </span>
                    <span className="text-data text-fg-muted">
                      {s.pct.toFixed(1)}%
                    </span>
                  </span>
                  <span className="font-mono text-data nums text-fg">
                    {formatMoney(s.marketValue)}
                  </span>
                </div>
                {/* Bar */}
                <div className="mt-0.5 h-1 w-full rounded-[1px] bg-surface-sunken">
                  <div
                    className="h-full rounded-[1px] transition-none"
                    style={{
                      width: `${s.pct}%`,
                      backgroundColor: barColour(s.gainPct),
                    }}
                  />
                </div>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}