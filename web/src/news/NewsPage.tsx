/**
 * News: one chronological list of market headlines, attributed and quiet.
 *
 * The document is `GET /market/news?limit=60` — the largest index funds and
 * companies, newest first, deduped by the server. This page adds nothing to
 * that list and removes nothing from it: every readable row is painted, so the
 * count it prints is the count the payload carried.
 *
 * Five rules the page keeps:
 *
 * * **A day is a heading, not a separator.** `Today`, `Yesterday`, then the
 *   date; a day with one story still gets its heading.
 * * **No timestamp is not today.** An undated headline sorts last, prints no
 *   clock, and sits under `Undated` — the feeds never said it happened today.
 * * **The headline is the only outbound link.** It opens in a new tab with
 *   `rel="noopener noreferrer"`, and a URL the guard refuses renders as text
 *   rather than as an inert-looking anchor. There is no reader view and no
 *   modal: the story belongs to the publisher.
 * * **The row carries its own context.** Source name and symbol chips are in
 *   the row, and the anchor's accessible name repeats them, so a reader
 *   tabbing between sixty links hears which company and which outlet each one
 *   belongs to.
 * * **Every state occupies one reserved box**, so the answer landing cannot
 *   move the page under the pointer.
 */

import * as React from "react";

import { formatCount } from "@/breadth/format";
import { Link } from "@/shell/routes";

import { itemClock, safeExternalHref, todayUtc } from "./format";
import {
  filterItems,
  groupByDay,
  type NewsGroup,
  type NewsItem,
} from "./news";
import { useNews } from "./useNews";

/** Skeleton rows: enough to draw the shape, never a fake set of headlines. */
export const NEWS_SKELETON_ROWS = 10;

/** A resolved row's height, in px: headline line plus the source/chip line. */
const NEWS_ROW_HEIGHT = 30;

/**
 * The box every state occupies, in px: ten rows plus the frame's padding and
 * border. The resolved list is taller; the floor is what stops the headings and
 * the filter box from starting lower down the page than the placeholder did.
 */
export const NEWS_RESERVED_HEIGHT = NEWS_SKELETON_ROWS * NEWS_ROW_HEIGHT + 18;

function Skeleton() {
  return (
    <div
      data-testid="news-skeleton"
      aria-busy="true"
      style={{ minHeight: NEWS_RESERVED_HEIGHT }}
      className="px-1 py-1.5"
    >
      <span className="sr-only">Loading the news feed</span>
      {Array.from({ length: NEWS_SKELETON_ROWS }, (_, index) => (
        <div
          key={index}
          aria-hidden="true"
          data-testid="news-skeleton-row"
          className="grid h-[30px] grid-cols-[42px_minmax(0,1fr)] items-center gap-x-2 border-b border-line/60 last:border-b-0"
        >
          <span className="block h-[9px] w-[30px] rounded-[1px] bg-surface-raised" />
          <span className="flex min-w-0 flex-col gap-1">
            <span
              className={
                index % 2 === 0
                  ? "block h-[9px] w-[72%] rounded-[1px] bg-surface-raised"
                  : "block h-[9px] w-[54%] rounded-[1px] bg-surface-raised"
              }
            />
            <span className="block h-[7px] w-[26%] rounded-[1px] bg-surface-raised" />
          </span>
        </div>
      ))}
    </div>
  );
}

function Row({ item }: { readonly item: NewsItem }) {
  const clock = itemClock(item.publishedAt);
  const href = safeExternalHref(item.url);

  // The context a sighted reader gets from the row's second line, said once for
  // a screen reader inside the link. `sr-only` keeps it out of the visual text;
  // it is part of the anchor's accessible name, not a separate announcement.
  const context = [
    item.source.length > 0 ? `Source: ${item.source}.` : "",
    item.symbols.length > 0 ? `Symbols: ${item.symbols.join(", ")}.` : "",
  ]
    .filter((part) => part.length > 0)
    .join(" ");

  return (
    <li
      data-testid="news-row"
      data-symbols={item.symbols.join(" ")}
      className="grid grid-cols-[42px_minmax(0,1fr)] items-start gap-x-2 border-b border-line/60 py-1.5 last:border-b-0"
    >
      {clock ? (
        <time
          data-testid="news-time"
          dateTime={item.publishedAt ?? undefined}
          title={clock.instant}
          className="nums pt-[2px] text-data text-fg-subtle"
        >
          {clock.clock}
        </time>
      ) : (
        // No `<time>` at all: there is no instant to mark up, and a dash in a
        // time element would announce a date that does not exist.
        <span aria-hidden="true" data-testid="news-no-time" className="pt-[2px] text-data text-fg-subtle">
          —
        </span>
      )}

      <div className="min-w-0">
        {href ? (
          <a
            data-testid="news-headline-link"
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="break-words text-ui leading-snug text-fg underline-offset-2 hover:text-accent-text hover:underline focus-visible:text-accent-text"
          >
            {item.headline}
            {context.length > 0 ? <span className="sr-only">. {context}</span> : null}
          </a>
        ) : (
          <span
            data-testid="news-plain"
            className="break-words text-ui leading-snug text-fg"
          >
            {item.headline}
            {context.length > 0 ? <span className="sr-only">. {context}</span> : null}
          </span>
        )}

        <div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1">
          {item.source.length > 0 ? (
            <span
              data-testid="news-source"
              className="text-label uppercase tracking-[0.04em] text-fg-subtle"
            >
              {item.source}
            </span>
          ) : null}
          {item.symbols.map((symbol) => (
            <Link
              key={symbol}
              to={`/stock/${encodeURIComponent(symbol)}`}
              data-testid="news-chip"
              aria-label={`${symbol} stock page`}
              className="focus-ring rounded-[2px] border border-line bg-surface-raised px-1 py-px font-mono text-label leading-none text-fg-muted no-underline hover:border-line-strong hover:text-fg"
            >
              {symbol}
            </Link>
          ))}
        </div>
      </div>
    </li>
  );
}

function Day({ group }: { readonly group: NewsGroup }) {
  const headingId = `news-day-${group.key ?? "undated"}`;

  return (
    <section
      data-testid={`news-group-${group.key ?? "undated"}`}
      data-day={group.key ?? undefined}
      aria-labelledby={headingId}
      className="mb-1.5 last:mb-0"
    >
      <h2
        id={headingId}
        className="flex min-w-0 items-baseline gap-1.5 px-1 py-1 text-data font-medium text-fg"
      >
        {group.key === null ? (
          <span>{group.label}</span>
        ) : (
          <time dateTime={group.key}>{group.label}</time>
        )}
        <span aria-hidden="true" className="text-fg-subtle">
          ·
        </span>
        <span className="nums text-label text-fg-subtle">
          {formatCount(group.items.length)}
        </span>
      </h2>
      <ul data-testid="news-list" className="flex flex-col">
        {group.items.map((item, index) => (
          <Row key={`${group.key ?? "undated"}:${index}:${item.url}`} item={item} />
        ))}
      </ul>
    </section>
  );
}

export interface NewsPageProps {
  /**
   * Today, as `YYYY-MM-DD` in UTC. Omitted in the app; pinned by tests and
   * previews so `Today`/`Yesterday` do not depend on the wall clock.
   */
  readonly today?: string;
  /** The endpoint's cap; 60 unless a preview asks otherwise. */
  readonly limit?: number;
}

export function NewsPage({ today, limit }: NewsPageProps = {}) {
  const news = useNews({ limit });
  const [query, setQuery] = React.useState("");

  const day = today ?? todayUtc();
  const report = news.report;
  const items = React.useMemo(() => report?.items ?? [], [report]);
  const visible = React.useMemo(() => filterItems(items, query), [items, query]);
  const groups = React.useMemo(() => groupByDay(visible, day), [visible, day]);

  const trimmedQuery = query.trim();
  const filtered = trimmedQuery.length > 0;
  const resultText = filtered
    ? `${formatCount(visible.length)} of ${formatCount(items.length)} match “${trimmedQuery}”`
    : `${formatCount(items.length)} headlines`;

  return (
    <main
      data-testid="news-page"
      className="min-h-0 flex-1 overflow-y-auto bg-bg px-2 py-2"
    >
      <header className="mb-1.5 px-0.5">
        <h1 className="text-ui font-medium text-fg">News</h1>
        <p className="mt-0.5 text-data text-fg-muted">
          Headlines about the largest index funds and companies, newest first. Times are
          UTC.
        </p>
      </header>

      {news.status === "ready" && items.length > 0 ? (
        <div className="mb-1.5 flex min-w-0 flex-wrap items-center gap-2 px-0.5">
          <label htmlFor="news-filter" className="sr-only">
            Filter headlines
          </label>
          <input
            id="news-filter"
            data-testid="news-filter"
            type="search"
            autoComplete="off"
            spellCheck={false}
            placeholder="Filter by headline or symbol"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="focus-ring h-6 w-[240px] rounded-sm border border-line bg-surface px-2 text-data text-fg placeholder:text-fg-disabled"
          />
          <p
            data-testid="news-filter-status"
            role="status"
            aria-live="polite"
            aria-atomic="true"
            className="min-w-0 truncate text-data text-fg-subtle"
          >
            {resultText}
          </p>
        </div>
      ) : null}

      <div
        data-testid="news-frame"
        style={{ minHeight: NEWS_RESERVED_HEIGHT }}
        className="rounded-md border border-line bg-surface"
      >
        {news.status === "loading" ? (
          <Skeleton />
        ) : news.status === "error" ? (
          <div
            data-testid="news-error"
            role="alert"
            className="flex items-start justify-between gap-3 px-3 py-2"
          >
            <div className="min-w-0">
              <p className="text-data text-fg">The news feed did not load.</p>
              <p className="mt-0.5 text-data text-fg-muted">{news.error}</p>
            </div>
            <button
              type="button"
              onClick={news.retry}
              className="focus-ring shrink-0 rounded-sm border border-line px-2 py-0.5 text-data text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
            >
              Retry
            </button>
          </div>
        ) : items.length === 0 ? (
          <div data-testid="news-empty" className="px-3 py-2">
            <p className="text-data text-fg">The feeds returned no headlines.</p>
            <p className="mt-0.5 text-data text-fg-muted">
              Nothing arrived from the index or company feeds on this refresh.
            </p>
          </div>
        ) : visible.length === 0 ? (
          <div data-testid="news-filter-empty" className="px-3 py-2">
            <p className="text-data text-fg">
              No headlines match “{trimmedQuery}”.
            </p>
            <p className="mt-0.5 text-data text-fg-muted">
              Filter by a word in the headline or by a ticker in the chips.
            </p>
          </div>
        ) : (
          <div data-testid="news-groups" className="px-1 py-0.5">
            {groups.map((group) => (
              <Day key={group.key ?? "undated"} group={group} />
            ))}

            {report && report.providerErrors.length > 0 ? (
              // Quiet, but not silent: a partial feed is a different claim
              // from a complete one, and the reader is told the list may be
              // missing rows without every provider error being printed.
              <p
                data-testid="news-provider-errors"
                className="px-1 py-1 text-label text-fg-subtle"
              >
                {formatCount(report.providerErrors.length)}{" "}
                {report.providerErrors.length === 1 ? "feed" : "feeds"} did not answer;
                this list may be missing headlines.
              </p>
            ) : null}
          </div>
        )}
      </div>
    </main>
  );
}
