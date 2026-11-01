/**
 * The page's first line: the newest headline, when it was filed, and who filed
 * it.
 *
 * The headline is a real `<a>` with `target="_blank"` and
 * `rel="noopener noreferrer"`, so a middle-click works and the opened tab cannot
 * reach back through `window.opener`. The URL is sanitised in the parser
 * (`safeExternalHref`), and a story whose link is not http(s) still prints its
 * headline as text — a headline is information even when its link is not
 * followable.
 *
 * The headline is rendered as a text node, never as markup: a feed that carried
 * `<script>` in a title is shown as the characters a reader can see, which is
 * both correct and the only safe reading of an untrusted string.
 *
 * Below 700 px the title and its stamp stack: one line of both at 390 px would
 * truncate the story to four words to keep a date visible, and the date is the
 * less important half.
 */

import { formatDayLabel } from "@/news/format";

import type { BlockState, HomeNews } from "../home";
import { BLOCK_HEIGHT, BlockMessage, Card } from "./HomeBlock";

/** `09:21 UTC · 31 Oct 2026`, or `""` when the feed did not stamp the story. */
export function stampOf(iso: string | null): string {
  if (iso === null || iso.trim().length === 0) return "";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "";
  const utc = parsed.toISOString();
  return `${utc.slice(11, 16)} UTC · ${formatDayLabel(utc.slice(0, 10))}`;
}

/**
 * How many headlines the bar draws in total: the lead plus seven below it.
 *
 * The dashboard asks the endpoint for eight, so the whole block is one fetch and
 * nothing here is a second request. Eight is a desk, not a feed: enough to look
 * like a news surface, few enough that the block stays one screen tall.
 */
const MORE_HEADLINES = 8;

export interface HeadlineBarProps {
  readonly state: BlockState;
  readonly news: readonly HomeNews[] | null;
  readonly className?: string;
}

export function HeadlineBar({ state, news, className }: HeadlineBarProps) {
  const newest = news !== null && news.length > 0 ? news[0] : null;

  return (
    <Card
      id="headline"
      title="Top headline"
      className={className}
      minHeight={BLOCK_HEIGHT.headline}
    >
      {state.kind === "loading" ? (
        <div aria-hidden="true" data-testid="headline-skeleton" className="flex min-w-0 flex-col gap-1 px-2 py-1">
          <span className="block h-[10px] w-[68%] rounded-[1px] bg-surface-raised" />
        </div>
      ) : state.kind === "error" || news === null ? (
        <BlockMessage
          id="headline"
          failed={state.kind === "error"}
          reason={state.reason ?? "Headlines unavailable."}
        />
      ) : newest === null ? (
        <p className="px-2 py-1 text-[11px] text-fg-subtle">No headlines in this cycle.</p>
      ) : (
        <div className="flex min-w-0 flex-col gap-1 px-2 py-1.5">
          {/*
           * The lead: the newest story, at reading size, with the stamp and the
           * wire under it. A dashboard's first line is the one a reader's eye
           * lands on, so it is a headline rather than a label.
           */}
          <div className="flex min-w-0 items-start gap-2">
            <span
              aria-hidden="true"
              className="mt-[3px] shrink-0 rounded-[2px] border border-accent/40 px-1 py-[1px] text-[9px] font-semibold uppercase tracking-[0.08em] text-accent-text"
            >
              Latest
            </span>
            {newest.url === null ? (
              <span
                data-testid="headline-text"
                title={newest.headline}
                className="min-w-0 text-[14px] font-semibold leading-snug text-fg line-clamp-2"
              >
                {newest.headline}
              </span>
            ) : (
              <a
                data-testid="headline-link"
                href={newest.url}
                target="_blank"
                rel="noopener noreferrer"
                title={newest.headline}
                className="min-w-0 text-[14px] font-semibold leading-snug text-fg hover:text-accent-text line-clamp-2"
              >
                {newest.headline}
              </a>
            )}
          </div>

          <span className="flex min-w-0 shrink-0 items-baseline gap-1 pl-[46px] text-[10px] text-fg-subtle">
            {newest.publishedAt === null ? null : (
              <time dateTime={newest.publishedAt}>{stampOf(newest.publishedAt)}</time>
            )}
            {newest.source.length > 0 ? (
              <>
                <span aria-hidden="true">·</span>
                <span className="truncate uppercase tracking-[0.05em]" title={newest.source}>
                  {newest.source}
                </span>
              </>
            ) : null}
          </span>

          {/*
           * The rest of the desk: the next stories, two columns, each with its
           * own time and wire. This is what makes the block read as a news
           * surface rather than a single banner — and every row is a real link
           * out, not a second copy of the lead.
           */}
          {news.length > 1 ? (
            <ul
              data-testid="headline-more"
              className="mt-1 grid min-w-0 gap-x-4 gap-y-0.5 border-t border-line pt-1.5 min-[900px]:grid-cols-2"
            >
              {news.slice(1, MORE_HEADLINES).map((item, index) => (
                <li
                  key={`${item.url ?? item.headline}-${index}`}
                  data-testid="headline-item"
                  className="flex min-w-0 items-baseline gap-1.5"
                >
                  <span className="w-[30px] shrink-0 text-[9px] text-fg-subtle">
                    {item.publishedAt === null ? "—" : item.publishedAt.slice(11, 16)}
                  </span>
                  {item.url === null ? (
                    <span className="min-w-0 truncate text-[11px] text-fg-muted" title={item.headline}>
                      {item.headline}
                    </span>
                  ) : (
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      title={item.headline}
                      className="min-w-0 truncate text-[11px] text-fg-muted hover:text-accent-text"
                    >
                      {item.headline}
                    </a>
                  )}
                  {item.source.length > 0 ? (
                    <span
                      className="hidden shrink-0 text-[9px] uppercase tracking-[0.05em] text-fg-subtle min-[700px]:inline"
                      title={item.source}
                    >
                      {item.source}
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      )}
    </Card>
  );
}
