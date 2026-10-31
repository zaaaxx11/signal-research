import * as React from "react";

import { Button } from "@/components/ui/button";
import { RadioTowerIcon, TriangleAlertIcon } from "lucide-react";

import { cn } from "@/lib/utils";

import { SectorBlock } from "./SectorBlock";
import { barLabel, barShare, extremeLabel, formatAsOf, formatCount, signedPercent } from "./format";
import { sumCheck, type BreadthReport } from "./report";
import { useBreadth, type BreadthView } from "./useBreadth";

/**
 * The market-wide strip above the grid.
 *
 * **Not mounted.** Home's signal cards replaced it as the market summary, and
 * the screener now opens directly on its page of rows — the operator's read was
 * that a strip of counts, a bar and twenty-one sector rows is noise above a
 * table that already answers "what is moving". The component is kept because
 * its per-sector blocks are the Groups page's material (one sector per line,
 * counts and averages), and its tests still pin that behaviour. Nothing imports
 * it, so it costs the bundle nothing; when Groups ships, it mounts there.
 *
 * It reads `GET /market/breadth` — the server's one-pass count over the whole
 * scanned universe — instead of counting the ≤20 rows the grid happens to
 * show. Those are different populations with different denominators, and the
 * old client-side breadth printed the second while implying the first.
 *
 * The strip prints what the endpoint returns and computes nothing of it:
 * advancing / declining / unchanged, a two-segment bar over the moving names,
 * the 52-week extremes **with the denominators they were counted against**, the
 * universe total and the scan time. When the server marks the snapshot stale,
 * the badge says so; when the scanner is down (503) the strip says that instead
 * of drawing a market of zeros.
 */

interface MetricProps {
  readonly label: string;
  readonly value: number;
  readonly testId: string;
  readonly tone: string;
  readonly title?: string;
}

function Metric({ label, value, testId, tone, title }: MetricProps) {
  return (
    <div className="flex items-baseline gap-1.5" title={title}>
      <dt className="text-label uppercase tracking-[0.08em] text-fg-subtle">{label}</dt>
      <dd data-testid={testId} className={cn("nums text-ui", tone)}>
        {formatCount(value)}
      </dd>
    </div>
  );
}

/**
 * Two segments, advancing against declining.
 *
 * The shares are presentation, not analysis: they divide the two counts the
 * endpoint already split out by their own sum. `movers === 0` is the case that
 * used to print `NaN%` — here the track renders flat and the accessible name
 * says no name moved.
 */
function AdvanceDeclineBar({ report }: { readonly report: BreadthReport }) {
  const movers = report.advancing + report.declining;
  const up = barShare(report.advancing, movers);
  const down = barShare(report.declining, movers);

  return (
    <div
      role="img"
      aria-label={barLabel(report)}
      data-testid="breadth-bar"
      className="relative mt-1.5 h-1.5 w-full overflow-hidden rounded-sm bg-surface-raised"
    >
      {movers > 0 ? (
        <>
          <span
            aria-hidden="true"
            data-testid="breadth-bar-up"
            className="absolute inset-y-0 left-0 bg-up"
            style={{ width: `${up}%` }}
          />
          <span
            aria-hidden="true"
            data-testid="breadth-bar-down"
            className="absolute inset-y-0 bg-down"
            style={{ left: `${up}%`, width: `${down}%` }}
          />
        </>
      ) : (
        <span aria-hidden="true" data-testid="breadth-bar-flat" className="absolute inset-0 bg-flat" />
      )}
    </div>
  );
}

function StaleBadge() {
  return (
    <span
      data-testid="breadth-stale"
      title="The server marked this snapshot stale: it is serving an earlier scan, not a fresh one."
      className="inline-flex shrink-0 items-center gap-1 rounded-sm border border-alert/60 bg-alert/10 px-1.5 py-0.5 text-label uppercase tracking-[0.08em] text-alert"
    >
      <span aria-hidden="true" className="size-1 rounded-full bg-alert" />
      Stale snapshot
    </span>
  );
}

function LoadingSkeleton() {
  return (
    <div
      aria-busy="true"
      aria-live="polite"
      data-testid="breadth-skeleton"
      className="flex min-w-0 flex-wrap items-start gap-x-6 gap-y-2 px-3 pb-2 pt-1.5"
    >
      <span className="sr-only">Loading market breadth</span>
      <div aria-hidden="true" className="min-w-[260px] flex-1 space-y-1.5">
        <div className="flex gap-3">
          <div className="h-4 w-16 animate-pulse rounded-sm bg-accent" />
          <div className="h-4 w-16 animate-pulse rounded-sm bg-accent" />
          <div className="h-4 w-16 animate-pulse rounded-sm bg-accent" />
          <div className="h-4 w-14 animate-pulse rounded-sm bg-accent" />
        </div>
        <div className="h-1.5 w-full animate-pulse rounded-sm bg-accent" />
      </div>
      <div aria-hidden="true" className="min-w-[220px] flex-1 space-y-1.5">
        <div className="h-3.5 w-full animate-pulse rounded-sm bg-accent" />
        <div className="h-3.5 w-full animate-pulse rounded-sm bg-accent" />
      </div>
      <div aria-hidden="true" className="w-full space-y-1">
        <div className="h-2.5 w-24 animate-pulse rounded-sm bg-accent" />
        <div className="grid grid-cols-[repeat(auto-fill,minmax(8.5rem,1fr))] gap-1">
          {Array.from({ length: 21 }, (_, index) => (
            <div key={index} className="h-8 animate-pulse rounded-sm bg-accent" />
          ))}
        </div>
      </div>
    </div>
  );
}

interface FailureNoticeProps {
  readonly view: BreadthView;
  readonly testId: string;
  readonly title: string;
  readonly detail: string;
  readonly icon: typeof RadioTowerIcon;
}

function FailureNotice({ view, testId, title, detail, icon: Icon }: FailureNoticeProps) {
  return (
    <div data-testid={testId} data-status={view.statusCode ?? undefined} className="px-3 pb-2 pt-1.5">
      <div className="flex min-w-0 items-start gap-2 rounded-sm border border-line border-l-2 border-l-alert bg-surface px-2.5 py-1.5">
        <Icon aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-alert" />
        <div className="min-w-0 flex-1">
          <p className="text-data font-medium text-fg">{title}</p>
          <p className="mt-0.5 text-data leading-relaxed text-fg-muted">{detail}</p>
          {view.message ? (
            <p className="mt-1 line-clamp-2 font-mono text-label text-fg-subtle" title={view.message}>
              {view.message}
            </p>
          ) : null}
        </div>
        <Button variant="outline" size="xs" onClick={view.reload} className="focus-ring focus-visible:ring-0">
          Retry
        </Button>
      </div>
    </div>
  );
}

function BreadthBody({ view }: { readonly view: BreadthView }) {
  const report = view.report;

  if (view.status === "unavailable" || (view.status === "ready" && report === null)) {
    return (
      <FailureNotice
        view={view}
        testId="breadth-unavailable"
        title="Scanner unavailable"
        detail="The market scanner could not answer, so no breadth is shown. An unread market is not a flat market — these counts stay blank rather than dropping to zero."
        icon={RadioTowerIcon}
      />
    );
  }

  if (view.status === "failed") {
    return (
      <FailureNotice
        view={view}
        testId="breadth-failed"
        title="Market breadth could not be read"
        detail="The breadth snapshot did not arrive, or did not parse, so nothing is shown rather than a guess."
        icon={TriangleAlertIcon}
      />
    );
  }

  if (view.status === "loading" || report === null) return <LoadingSkeleton />;

  const check = sumCheck(report);
  const unchangedTitle =
    report.unchanged_missing !== null && report.unchanged_missing > 0
      ? `${formatCount(report.unchanged_missing)} of these rows carry no reported change at all; they are counted here, never as a decline`
      : "Rows that printed exactly 0.00%";

  return (
    <div className="px-3 pb-1.5 pt-1">
      <div className="flex min-w-0 flex-wrap items-start gap-x-6 gap-y-2">
        <div className="min-w-[260px] flex-1">
          <dl className="flex min-w-0 flex-wrap items-baseline gap-x-4 gap-y-1">
            <Metric
              label="Advancing"
              value={report.advancing}
              testId="breadth-advancing"
              tone="text-up"
            />
            <Metric
              label="Declining"
              value={report.declining}
              testId="breadth-declining"
              tone="text-down"
            />
            <Metric
              label="Unchanged"
              value={report.unchanged}
              testId="breadth-unchanged"
              tone="text-flat"
              title={unchangedTitle}
            />
            <Metric
              label="Universe"
              value={report.universe_total}
              testId="breadth-universe"
              tone="text-fg"
            />
          </dl>
          <AdvanceDeclineBar report={report} />
        </div>

        <div className="min-w-[220px]">
          <h3 className="text-label uppercase tracking-[0.1em] text-fg-subtle">
            52-week extremes
          </h3>
          <dl className="mt-1 flex flex-col gap-0.5">
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-data text-fg-muted">New highs</dt>
              <dd
                data-testid="breadth-new-highs"
                className="nums text-data text-up"
                title={report.method["new_highs"]}
              >
                {extremeLabel(report.new_highs, report.new_highs_denominator)}
              </dd>
            </div>
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-data text-fg-muted">New lows</dt>
              <dd
                data-testid="breadth-new-lows"
                className="nums text-data text-down"
                title={report.method["new_lows"]}
              >
                {extremeLabel(report.new_lows, report.new_lows_denominator)}
              </dd>
            </div>
          </dl>
        </div>
      </div>

      {check.ok ? null : (
        // The counts do not add up: an alert, not a status. It is a warning
        // about the table above it, and the one status role in the app is the
        // row range in `ScreenPanel`.
        <p
          role="alert"
          data-testid="breadth-sum-warning"
          className="mt-2 flex items-start gap-1.5 rounded-sm border border-alert/60 border-l-2 border-l-alert bg-alert/10 px-2 py-1 text-data text-alert"
        >
          <TriangleAlertIcon aria-hidden="true" className="mt-0.5 size-3 shrink-0" />
          <span>
            Counts do not add up: advancing + declining + unchanged ={" "}
            {formatCount(check.sum)}, but the scan reports a universe of{" "}
            {formatCount(check.total)} ({signedPercent(check.delta).text}). The snapshot is
            inconsistent — treat these figures as provisional.
          </span>
        </p>
      )}

      <SectorBlock
        sectors={report.sectors}
        sectorMissing={report.sector_missing}
        className="mt-1.5"
      />
    </div>
  );
}

/**
 * The strip's settled height, in px.
 *
 * Measured in Chromium at 1 280 px against the live endpoint: the resolved
 * strip is 223 px, the skeleton was 196 px, so a slow `/market/breadth` pushed
 * the grid down 27 px when it landed (layout-shift 0.0125 — "good" by CLS, and
 * still a visible jump while the scanner is slow). The floor is applied to the
 * section for **every** state rather than to the skeleton alone: a resolved
 * strip that happens to be shorter (a wider viewport packs the sector grid into
 * fewer rows) would otherwise shift the page in the other direction.
 */
export const BREADTH_RESERVED_HEIGHT = 223;

export interface BreadthStripProps {
  readonly region?: string;
  readonly assetClass?: string;
  readonly className?: string;
}

export function BreadthStrip({ region, assetClass, className }: BreadthStripProps) {
  const view = useBreadth({ region, assetClass });
  const headingId = React.useId();

  const scope = [view.report?.region, view.report?.asset_class]
    .filter((part): part is string => typeof part === "string" && part.length > 0)
    .join(" · ");

  return (
    <section
      aria-labelledby={headingId}
      data-testid="breadth-strip"
      data-state={view.status}
      data-stale={view.report?.stale === true ? "true" : "false"}
      style={{ minHeight: BREADTH_RESERVED_HEIGHT }}
      className={cn(
        "min-w-0 shrink-0 overflow-hidden border-b border-line bg-surface-sunken",
        className,
      )}
    >
      <header className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1 px-3 pt-1.5">
        <h2
          id={headingId}
          className="flex min-w-0 items-baseline gap-2 text-label uppercase tracking-[0.1em] text-fg-subtle"
        >
          Market breadth
          {scope ? <span className="truncate text-fg-muted">{scope}</span> : null}
        </h2>
        {view.report ? (
          <div className="flex shrink-0 items-baseline gap-2 font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
            {view.report.stale ? <StaleBadge /> : null}
            <time
              data-testid="breadth-as-of"
              dateTime={view.report.as_of}
              title={view.report.as_of}
            >
              {"as of "}
              {formatAsOf(view.report.as_of)}
            </time>
          </div>
        ) : null}
      </header>

      <BreadthBody view={view} />
    </section>
  );
}
