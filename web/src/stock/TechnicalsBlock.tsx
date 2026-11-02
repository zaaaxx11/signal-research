/**
 * The Overview's technicals block: the numbers that used to live only in the
 * API, printed where the reader looks for them.
 *
 * Four compact tables in one responsive grid — moving averages (SMA and EMA
 * side by side, the two value columns), oscillators, returns and the
 * benchmark-relative risk group. Every group's caption names its window, so
 * "MTD" cannot be mistaken for "since inception" and Alpha cannot be read as
 * Alpha versus anything but the S&P 500 over one year of daily closes.
 *
 * The block owns two independent reads. A failure is one reasoned sentence in
 * the failed group's place: the other group keeps its numbers and the rest of
 * the ticker page is never touched.
 */

import * as React from "react";

import { cn } from "@/lib/utils";

import {
  MA_WINDOWS,
  metricCell,
  OSCILLATOR_ROWS,
  RETURN_ROWS,
  RISK_ROWS,
  type MetricDocument,
  type MetricRow,
} from "./technicals";
import { useRisk, useTechnicals, type MetricResource } from "./useTicker";

/** The one-line window under each daily-close group. */
export const DAILY_CAPTION = "Daily closes, 1 year";

/** The risk group is measured against this benchmark, at this frequency. */
export const RISK_CAPTION = "vs S&P 500, 1Y daily";

export interface TechnicalsBlockProps {
  readonly symbol: string;
  readonly className?: string;
}

function GroupCaption({
  title,
  caption,
}: {
  title: string;
  caption: string;
}): React.ReactElement {
  return (
    <caption className="pb-1 text-left">
      <span className="block text-label font-medium uppercase tracking-[0.08em] text-fg-subtle">
        {title}
      </span>
      <span className="block text-label text-fg-subtle">{caption}</span>
    </caption>
  );
}

function MetricValue({
  document,
  metricKey,
  signed = false,
}: {
  document: MetricDocument | null;
  metricKey: string;
  signed?: boolean;
}): React.ReactElement {
  const cell = metricCell(document, metricKey, { signed });
  return (
    <td
      data-technical={metricKey}
      data-absent={cell.absent ? "true" : "false"}
      data-tone={cell.tone ?? "none"}
      className={cn(
        "nums truncate text-right font-mono text-data leading-5 tabular-nums",
        cell.absent && "text-fg-subtle",
        cell.tone === "up" && "text-up",
        cell.tone === "down" && "text-down",
        !cell.absent && cell.tone === null && "text-fg",
      )}
    >
      {cell.text}
    </td>
  );
}

/** Moving averages share one row per window: the label plus SMA and EMA. */
function MovingAverages({ document }: { document: MetricDocument }): React.ReactElement {
  return (
    <table
      data-technical-group="moving-averages"
      className="col-span-2 w-full min-w-0 table-fixed border-collapse min-[900px]:col-span-1"
    >
      <GroupCaption title="Moving averages" caption={DAILY_CAPTION} />
      <colgroup>
        <col className="w-[28%]" />
        <col className="w-[36%]" />
        <col className="w-[36%]" />
      </colgroup>
      <thead>
        <tr className="border-b border-line">
          <th
            scope="col"
            className="pb-1 text-left text-label font-medium uppercase tracking-[0.06em] text-fg-subtle"
          >
            Window
          </th>
          <th
            scope="col"
            className="pb-1 text-right text-label font-medium uppercase tracking-[0.06em] text-fg-subtle"
          >
            SMA
          </th>
          <th
            scope="col"
            className="pb-1 text-right text-label font-medium uppercase tracking-[0.06em] text-fg-subtle"
          >
            EMA
          </th>
        </tr>
      </thead>
      <tbody>
        {MA_WINDOWS.map((window) => (
          <tr key={window} data-ma-window={window} className="h-5 border-b border-line/70 last:border-b-0">
            <th scope="row" className="nums text-left font-mono text-data font-normal text-fg-muted">
              {window}
            </th>
            <MetricValue document={document} metricKey={`sma_${window}`} />
            <MetricValue document={document} metricKey={`ema_${window}`} />
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function MetricTable({
  group,
  title,
  caption,
  rows,
  document,
  className,
}: {
  group: string;
  title: string;
  caption: string;
  rows: readonly MetricRow[];
  document: MetricDocument;
  className?: string;
}): React.ReactElement {
  return (
    <table
      data-technical-group={group}
      className={cn("w-full min-w-0 table-fixed border-collapse", className)}
    >
      <GroupCaption title={title} caption={caption} />
      <colgroup>
        <col className="w-[58%]" />
        <col className="w-[42%]" />
      </colgroup>
      <tbody>
        {rows.map((row) => (
          <tr key={row.key} className="h-5 border-b border-line/70 last:border-b-0">
            <th
              scope="row"
              className="max-w-0 truncate pr-2 text-left text-data font-normal leading-5 text-fg-muted"
            >
              {row.label}
            </th>
            <MetricValue document={document} metricKey={row.key} signed={row.signed} />
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Loading({ label }: { label: string }): React.ReactElement {
  return (
    <p aria-busy="true" className="py-2 text-data text-fg-subtle">
      {label}
    </p>
  );
}

function Failure({
  testId,
  title,
  state,
  className,
}: {
  testId: string;
  title: string;
  state: MetricResource;
  className?: string;
}): React.ReactElement {
  return (
    <div
      role="alert"
      data-testid={testId}
      className={cn("border border-line/70 bg-surface px-3 py-2", className)}
    >
      <p className="text-data text-fg-muted">{title}</p>
      {state.error ? (
        <p className="mt-0.5 break-words text-label text-fg-subtle">{state.error}</p>
      ) : null}
      <button
        type="button"
        onClick={state.retry}
        className="focus-ring mt-1 h-6 rounded-sm border border-line-control px-2 text-data text-fg-muted hover:text-fg"
      >
        Retry
      </button>
    </div>
  );
}

export function TechnicalsBlock({
  symbol,
  className,
}: TechnicalsBlockProps): React.ReactElement {
  const technicals = useTechnicals(symbol);
  const risk = useRisk(symbol);
  const status =
    technicals.status === "ready" && risk.status === "ready"
      ? "ready"
      : technicals.status === "loading" || risk.status === "loading"
        ? "loading"
        : "partial";

  return (
    <section
      data-testid="technicals-block"
      data-status={status}
      aria-labelledby="technicals-heading"
      className={cn("min-w-0", className)}
    >
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <h2
          id="technicals-heading"
          className="text-label font-medium uppercase tracking-[0.08em] text-fg-subtle"
        >
          Technicals
        </h2>
        <span className="hidden text-label text-fg-subtle min-[700px]:inline">
          Computed on read from the daily close series
        </span>
      </div>

      <div
        data-testid="technicals-grid"
        className="grid grid-cols-2 gap-x-5 gap-y-4 min-[900px]:grid-cols-4"
      >
        {technicals.status === "error" ? (
          <Failure
            testId="technicals-error"
            title="The technicals could not be loaded."
            state={technicals}
            className="col-span-2 min-[900px]:col-span-3"
          />
        ) : technicals.data === null ? (
          <div className="col-span-2 min-[900px]:col-span-3">
            <Loading label="Loading technicals…" />
          </div>
        ) : (
          <>
            <MovingAverages document={technicals.data} />
            <MetricTable
              group="oscillators"
              title="Oscillators"
              caption={DAILY_CAPTION}
              rows={OSCILLATOR_ROWS}
              document={technicals.data}
            />
            <MetricTable
              group="returns"
              title="Returns"
              caption={DAILY_CAPTION}
              rows={RETURN_ROWS}
              document={technicals.data}
            />
          </>
        )}

        {risk.status === "error" ? (
          <Failure
            testId="risk-error"
            title="The risk group could not be loaded."
            state={risk}
            className="col-span-2 min-[900px]:col-span-1"
          />
        ) : risk.data === null ? (
          <div className="col-span-2 min-[900px]:col-span-1">
            <Loading label="Loading risk…" />
          </div>
        ) : (
          <MetricTable
            group="risk"
            title="Risk"
            caption={RISK_CAPTION}
            rows={RISK_ROWS}
            document={risk.data}
            className="col-span-2 min-[900px]:col-span-1"
          />
        )}
      </div>
    </section>
  );
}
