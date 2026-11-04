import { useMemo } from "react";

import {
  monthStartUTC,
  dayStartUTC,
} from "./aggregate";
import type { Bar } from "./types";

/**
 * The calendar view: the daily tape laid out on real calendar months, one cell
 * per day the market traded, coloured by the day's own move with the product's
 * automated rule — up green, down red, and the intensity of the move drives
 * the tint (a flat day barely tints, a ±4% or bigger day at full 60%).
 *
 * The reader asks "how did the month actually look" — not "where was price" —
 * so the cell is a colour with a number in it, a range slider would lie, and
 * the windows are monthly blocks in reading order with weekday headers.
 */
export function tintAlpha(changePct: number | null | undefined): number {
  if (changePct == null || !Number.isFinite(changePct)) {
    return 0.05;
  }
  return Math.min(0.6, Math.max(0.05, Math.abs(changePct) / 4));
}

function dayRow(bar: Bar, previousClose: number | null) {
  const pct = previousClose !== null && previousClose !== 0
    ? ((bar.c - previousClose) / previousClose) * 100
    : null;
  return { bar, pct };
}

const WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"] as const;

export function CalendarChart({
  bars,
  previousClose,
  testId = "calendar-chart",
}: {
  bars: readonly Bar[];
  previousClose: number | null;
  testId?: string;
}) {
  const days = useMemo(() => {
    const rows: ReturnType<typeof dayRow>[] = [];
    for (let i = 0; i < bars.length; i += 1) {
      const before = i > 0 ? bars[i - 1].c : previousClose;
      rows.push(dayRow(bars[i], before));
    }
    return rows;
  }, [bars, previousClose]);

  const months = useMemo(() => {
    if (days.length === 0) return [];
    const out: { label: string; start: number; cells: Map<number, ReturnType<typeof dayRow>> }[] = [];
    let currentStart: number | null = null;
    let currentLabel = "";
    for (const row of days) {
      const first = monthStartUTC(row.bar.t);
      if (first !== currentStart) {
        out.push({
          label: new Date(first * 1000).toLocaleString("en", { month: "short", year: "numeric", timeZone: "UTC" }),
          start: first,
          cells: new Map(),
        });
        currentStart = first;
        currentLabel = "";
      }
      void currentLabel;
      out[out.length - 1].cells.set(dayStartUTC(row.bar.t), row);
    }
    return out;
  }, [days]);

  return (
    <div data-testid={testId} className="flex flex-col gap-3">
      {months.map((month) => {
        const first = new Date(month.start * 1000);
        const daysInMonth = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0)).getUTCDate();
        const lead = (new Date(month.start * 1000).getUTCDay() + 6) % 7;
        return (
          <section key={month.start} aria-label={`Calendar for ${month.label}`} data-testid={`calendar-month`}>
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-muted">
              {month.label}
            </div>
            <div className="grid grid-cols-7 gap-[2px]" role="grid">
              {WEEKDAYS.map((day) => (
                <div key={day} role="columnheader" className="pb-0.5 text-center text-[9px] text-fg-subtle">
                  {day}
                </div>
              ))}
              {Array.from({ length: lead }).map((_, pad) => (
                <div key={`pad-${pad}`} />
              ))}
              {Array.from({ length: daysInMonth }).map((_, dayIndex) => {
                const t = month.start + dayIndex * 86_400;
                const row = month.cells.get(t);
                if (row === undefined) return <div key={t} />;
                const up = (row.pct ?? 0) >= 0;
                const alpha = tintAlpha(row.pct) * 100;
                return (
                  <div
                    key={t}
                    role="gridcell"
                    data-testid={`calendar-day-${new Date(t * 1000).getUTCDate()}`}
                    title={`${new Date(t * 1000).toISOString().slice(0, 10)} — close ${row.bar.c}, ${row.pct?.toFixed(2) ?? "—"}%`}
                    className="flex min-h-[34px] flex-col justify-between rounded-[4px] p-1 text-[10px] leading-tight"
                    style={{
                      background: `color-mix(in srgb, ${up ? "var(--up)" : "var(--down)"} ${alpha.toFixed(0)}%, var(--surface-sunken))`,
                    }}
                  >
                    <span className="text-fg-subtle">{new Date(t * 1000).getUTCDate()}</span>
                    <span style={{ color: up ? "var(--up)" : "var(--down)" }}>
                      {row.pct !== null ? `${row.pct > 0 ? "+" : ""}${row.pct.toFixed(1)}%` : "—"}
                    </span>
                  </div>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}
