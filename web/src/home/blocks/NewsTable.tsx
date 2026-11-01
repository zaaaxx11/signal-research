/**
 * The desk: one scrolling table of headlines, the way a wire terminal shows it.
 *
 * A row is `date/time · headline · wire`. Stories from today print `HH:MM`;
 * anything older prints `MMM-DD`, which is the convention a reader of a terminal
 * already knows and the only one that fits a 64 px column without a second line.
 *
 * The table is the whole news surface on this page — there is no separate lead
 * banner and no second list — so it shows everything the endpoint sent (forty
 * rows today) inside a fixed-height scroll box. The box has its own height so
 * the page does not grow when a busy news day arrives; the scrollbar is the
 * signal that there is more.
 *
 * Every row is a real anchor with `target="_blank"` and
 * `rel="noopener noreferrer"`; a story whose URL did not survive the parser
 * prints as text rather than as a dead link.
 */

import type { BlockState, HomeNews } from "../home";
import { BLOCK_HEIGHT, HomeBlock } from "./HomeBlock";

/** `05:13` for today (UTC), `Oct-31` for anything older. */
export function whenLabel(iso: string | null, now: Date = new Date()): string {
  if (iso === null || iso.length < 10) return "—";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "—";
  const utc = parsed.toISOString();
  const today = now.toISOString().slice(0, 10);
  if (utc.slice(0, 10) === today) return utc.slice(11, 16);
  const month = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][
    Number(utc.slice(5, 7)) - 1
  ];
  return `${month}-${utc.slice(8, 10)}`;
}

export interface NewsTableProps {
  readonly state: BlockState;
  readonly news: readonly HomeNews[] | null;
  readonly className?: string;
}

export function NewsTable({ state, news, className }: NewsTableProps) {
  return (
    <HomeBlock
      id="news"
      title="Headlines"
      className={className}
      state={state}
      minHeight={BLOCK_HEIGHT.headline}
      actions={
        news === null ? null : (
          <span className="shrink-0 text-[10px] text-fg-subtle">{news.length}</span>
        )
      }
      skeleton={
        <div aria-hidden="true" className="flex flex-col gap-1 p-2">
          {Array.from({ length: 8 }, (_, index) => (
            <span
              key={index}
              className="block h-[10px] rounded-[1px] bg-surface-raised"
              style={{ width: `${92 - (index % 4) * 7}%` }}
            />
          ))}
        </div>
      }
    >
      {news === null ? null : news.length === 0 ? (
        <p className="px-2 py-1 text-[11px] text-fg-subtle">No headlines in this cycle.</p>
      ) : (
        <ul
          data-testid="news-table"
          className="max-h-[248px] min-w-0 overflow-y-auto [scrollbar-width:thin]"
        >
          {news.map((item, index) => (
            <li
              key={`${item.url ?? item.headline}-${index}`}
              data-testid="news-row"
              className="flex min-w-0 items-baseline gap-2 border-b border-line/60 px-2 py-[3px] last:border-b-0 hover:bg-row-hover"
            >
              <span className="nums w-[46px] shrink-0 text-[10px] text-fg-subtle">
                {whenLabel(item.publishedAt)}
              </span>
              {item.url === null ? (
                <span className="min-w-0 flex-1 truncate text-[11px] text-fg-muted" title={item.headline}>
                  {item.headline}
                </span>
              ) : (
                <a
                  href={item.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={item.headline}
                  className="min-w-0 flex-1 truncate text-[11px] text-fg hover:text-accent-text"
                >
                  {item.headline}
                </a>
              )}
              {item.source.length > 0 ? (
                <span
                  className="hidden w-[110px] shrink-0 truncate text-right text-[9px] uppercase tracking-[0.05em] text-fg-subtle min-[700px]:block"
                  title={item.source}
                >
                  {item.source}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </HomeBlock>
  );
}
