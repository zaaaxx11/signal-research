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
        <div className="flex min-w-0 flex-col justify-center gap-0.5 px-2 py-1 min-[700px]:flex-row min-[700px]:items-baseline min-[700px]:gap-2">
          {newest.url === null ? (
            <span
              data-testid="headline-text"
              title={newest.headline}
              className="min-w-0 truncate text-[12px] font-medium text-fg"
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
              className="min-w-0 truncate text-[12px] font-medium text-fg hover:text-accent-text"
            >
              {newest.headline}
            </a>
          )}

          <span className="flex min-w-0 shrink-0 items-baseline gap-1 text-[10px] text-fg-subtle">
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
        </div>
      )}
    </Card>
  );
}
