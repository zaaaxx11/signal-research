/**
 * Calendar: earnings, one section per day, forward in time.
 *
 * A left rail lists the days and counts so a reader can jump three weeks ahead
 * without scrolling the whole page; the body is a section per day, each one a
 * column of compact symbol rows (logo, ticker, company, and a real link to the
 * stock page).
 *
 * Four rules the page keeps:
 *
 * * **The day is the heading and the count is the claim.** `Mon 2 Nov · 42
 *   reports` prints the server's true total even when only fifty names are
 *   listed, and the remainder is stated as `+87 more` — never dropped, never
 *   implied to be the total.
 * * **A day that has passed, or holds nothing, is not a section.** The filter
 *   lives in `calendar.ts` so the rail, the body and the empty state cannot
 *   disagree about which days exist.
 * * **Every row is an anchor**, so the page is reachable with Tab, and a
 *   middle-click opens a real URL.
 * * **Loading, empty, error and the day list all occupy one reserved box**, so
 *   the answer landing cannot move the page.
 */

import * as React from "react";

import { formatCount } from "@/breadth/format";
import { LogoMark } from "@/grid/LogoMark";
import { Link } from "@/shell/routes";

import {
  CALENDAR_DAYS,
  CALENDAR_KINDS,
  dayLabel,
  overflowCount,
  reportLabel,
  shownSymbols,
  type CalendarBucket,
  type CalendarKind,
} from "./calendar";
import { useCalendar } from "./useCalendar";

/** Skeleton rows: a few days' worth, not a fake table. */
export const CALENDAR_SKELETON_ROWS = 8;

/**
 * The box every state occupies, in px: a 26 px heading strip plus eight 24 px
 * rows plus the frame's border. The resolved page is usually taller (the
 * window, not the skeleton, decides); the floor is what stops the answer from
 * starting higher up the page than the placeholder did.
 */
export const CALENDAR_RESERVED_HEIGHT = 26 + CALENDAR_SKELETON_ROWS * 24 + 2;

function Skeleton() {
  return (
    <div data-testid="calendar-skeleton" aria-busy="true" className="px-2 py-1.5">
      <span className="sr-only">Loading the earnings calendar</span>
      {Array.from({ length: CALENDAR_SKELETON_ROWS }, (_, index) => (
        <div
          key={index}
          aria-hidden="true"
          data-testid="calendar-skeleton-row"
          className="flex h-6 items-center gap-2 border-b border-line/60"
        >
          <span className="block h-[9px] w-[104px] rounded-[1px] bg-surface-raised" />
          <span
            className={
              index % 2 === 0
                ? "block h-[9px] w-[38%] rounded-[1px] bg-surface-raised"
                : "block h-[9px] w-[52%] rounded-[1px] bg-surface-raised"
            }
          />
        </div>
      ))}
    </div>
  );
}

function SymbolRow({ symbol }: { readonly symbol: { s: string; n: string } }) {
  const title = symbol.n.length > 0 ? `${symbol.s} — ${symbol.n}` : symbol.s;

  return (
    <li
      data-testid="calendar-symbol-row"
      className="flex h-6 min-w-0 items-center gap-1.5 px-0.5"
    >
      <Link
        to={`/stock/${encodeURIComponent(symbol.s)}`}
        title={title}
        className="flex min-w-0 shrink-0 items-center gap-1.5 text-fg hover:text-accent-text"
      >
        <LogoMark symbol={symbol.s} size={16} />
        <span className="font-mono text-data font-semibold">{symbol.s}</span>
      </Link>
      <span className="min-w-0 truncate text-data text-fg-muted" title={title}>
        {symbol.n}
      </span>
    </li>
  );
}

function Day({ bucket }: { readonly bucket: CalendarBucket }) {
  const shown = shownSymbols(bucket);
  const more = overflowCount(bucket);
  const headingId = `day-heading-${bucket.date}`;

  return (
    <section
      id={`day-${bucket.date}`}
      data-testid={`calendar-bucket-${bucket.date}`}
      data-day={bucket.date}
      aria-labelledby={headingId}
      className="scroll-mt-8 px-2 py-1.5"
    >
      <h2
        id={headingId}
        className="flex min-w-0 items-baseline gap-1.5 text-data font-medium text-fg"
      >
        <time dateTime={bucket.date}>{dayLabel(bucket.date)}</time>
        <span aria-hidden="true" className="text-fg-subtle">
          ·
        </span>
        <span className="nums text-fg-muted">{reportLabel(bucket.count)}</span>
      </h2>

      <ul className="mt-1 grid gap-x-6 gap-y-0 sm:grid-cols-2 xl:grid-cols-3">
        {shown.map((symbol) => (
          <SymbolRow key={`${bucket.date}:${symbol.s}`} symbol={symbol} />
        ))}
      </ul>

      {more > 0 ? (
        <p data-testid={`calendar-more-${bucket.date}`} className="mt-1 text-label text-fg-subtle">
          +{formatCount(more)} more
        </p>
      ) : null}
    </section>
  );
}

function Days({ buckets }: { readonly buckets: readonly CalendarBucket[] }) {
  return (
    <div data-testid="calendar-daylist" className="grid grid-cols-1 md:grid-cols-[168px_minmax(0,1fr)]">
      {/* The border lives on a stretching wrapper so the divider runs the full
          page height, while the rail itself sticks: across eighteen days the
          day list is the only navigation, and scrolling it away would leave the
          reader with no way to jump. */}
      <div className="border-b border-line md:border-b-0 md:border-r">
        <nav
          data-testid="calendar-rail"
          aria-label="Days with reports"
          className="bg-surface px-1.5 py-1.5 md:sticky md:top-0"
        >
          <ul className="flex flex-wrap gap-x-3 gap-y-0.5 md:flex-col md:gap-y-0">
            {buckets.map((bucket) => (
              <li key={bucket.date} className="min-w-0">
                <a
                  href={`#day-${bucket.date}`}
                  className="focus-ring flex items-baseline justify-between gap-2 rounded-sm px-1 py-0.5 no-underline hover:bg-row-hover"
                >
                  <time dateTime={bucket.date} className="truncate text-data text-fg-muted">
                    {dayLabel(bucket.date)}
                  </time>
                  <span className="nums shrink-0 text-label text-fg-subtle">
                    {formatCount(bucket.count)}
                  </span>
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </div>

      <div className="min-w-0 divide-y divide-line">
        {buckets.map((bucket) => (
          <Day key={bucket.date} bucket={bucket} />
        ))}
      </div>
    </div>
  );
}

export interface CalendarPageProps {
  readonly region?: string;
  readonly days?: number;
  /** Today, as `YYYY-MM-DD`. Omitted in the app; pinned by tests and previews. */
  readonly today?: string;
  readonly kind?: CalendarKind;
}

const KIND_LABELS: Record<CalendarKind, string> = {
  earnings: "Earnings",
  dividends: "Dividends",
  economic: "Economic",
};

function ComingSoon({ reason }: { readonly reason: string | null }) {
  return (
    <div
      data-testid="calendar-unavailable"
      className="flex flex-col items-center justify-center gap-2 px-3 py-6 text-center"
    >
      <p className="text-data text-fg-muted">
        {reason ?? "This calendar is not available yet."}
      </p>
    </div>
  );
}

export function CalendarPage(props: CalendarPageProps = {}) {
  const [kind, setKind] = React.useState<CalendarKind>(props.kind ?? "earnings");
  const calendar = useCalendar({ region: props.region, days: props.days, today: props.today, kind });
  const windowDays = props.days ?? CALENDAR_DAYS;

  return (
    <main
      data-testid="calendar-page"
      className="min-h-0 flex-1 overflow-y-auto bg-bg px-2 py-2"
    >
      <div className="mb-1.5 flex min-w-0 items-baseline justify-between gap-3 px-0.5">
        <div className="flex min-w-0 items-baseline gap-2">
          <h1 className="text-ui font-medium text-fg">Calendar</h1>
          <nav
            data-testid="calendar-tabs"
            aria-label="Calendar type"
            className="flex items-center gap-0.5"
          >
            {CALENDAR_KINDS.map((tabKind) => (
              <button
                key={tabKind}
                type="button"
                data-testid={`calendar-tab-${tabKind}`}
                aria-pressed={kind === tabKind}
                onClick={() => setKind(tabKind)}
                className={[
                  "focus-ring rounded-sm px-2 py-0.5 text-data font-medium transition-colors",
                  kind === tabKind
                    ? "text-fg"
                    : "text-fg-subtle hover:text-fg-muted",
                ].join(" ")}
              >
                {KIND_LABELS[tabKind]}
              </button>
            ))}
          </nav>
        </div>
        <p className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle">
          Next {windowDays} days
        </p>
      </div>

      {/* No `overflow-hidden` on the frame: it would become the rail's
          scrollport and stop `position: sticky` from following the page. The
          rounded corners are drawn by the border, and nothing inside paints a
          background that could square them off. */}
      <div
        data-testid="calendar-frame"
        style={{ minHeight: CALENDAR_RESERVED_HEIGHT }}
        className="rounded-md border border-line bg-surface"
      >
        {calendar.status === "loading" ? (
          <Skeleton />
        ) : calendar.status === "error" ? (
          <div
            data-testid="calendar-error"
            role="alert"
            className="flex items-start justify-between gap-3 px-3 py-2"
          >
            <div className="min-w-0">
              <p className="text-data text-fg">
                The {KIND_LABELS[kind].toLowerCase()} calendar did not load.
              </p>
              <p className="mt-0.5 text-data text-fg-muted">{calendar.error}</p>
            </div>
            <button
              type="button"
              onClick={calendar.retry}
              className="focus-ring shrink-0 rounded-sm border border-line px-2 py-0.5 text-data text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
            >
              Retry
            </button>
          </div>
        ) : calendar.status === "unavailable" ? (
          <ComingSoon reason={calendar.report?.reason ?? null} />
        ) : calendar.report === null || calendar.report.buckets.length === 0 ? (
          <p data-testid="calendar-empty" className="px-3 py-2 text-data text-fg-muted">
            No {KIND_LABELS[kind].toLowerCase()} events in the next {windowDays} days.
          </p>
        ) : (
          <Days buckets={calendar.report.buckets} />
        )}
      </div>
    </main>
  );
}
