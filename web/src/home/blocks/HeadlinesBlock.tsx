/**
 * The news list: five stories, each a real link in a new tab.
 *
 * The URL is already canonical or `null` (the parser ran it through
 * `safeExternalHref`), so the anchor can only be http(s). A story with no
 * followable link still appears — as text — because the headline is the
 * information and the link is transport.
 *
 * The headline is interpolated, never injected: a title that contains `<script>`
 * renders as those eight characters, exactly as it should for a string that came
 * off an RSS feed.
 */

import { Link } from "@/shell/routes";

import { formatDayLabel } from "@/news/format";

import type { BlockState, HomeNews } from "../home";
import { BLOCK_HEIGHT, HomeBlock, SkeletonRows } from "./HomeBlock";

/** `09:21 · 31 Oct` — short enough for a 4/12 column at 1440 px. */
export function shortStamp(iso: string | null): string {
  if (iso === null || iso.trim().length === 0) return "";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "";
  const utc = parsed.toISOString();
  const day = formatDayLabel(utc.slice(0, 10));
  return `${utc.slice(11, 16)} · ${day.replace(/ \d{4}$/, "")}`;
}

export interface HeadlinesBlockProps {
  readonly state: BlockState;
  readonly news: readonly HomeNews[] | null;
  readonly className?: string;
}

export function HeadlinesBlock({ state, news, className }: HeadlinesBlockProps) {
  const shown = news === null ? [] : news.slice(0, 5);

  return (
    <HomeBlock
      id="headlines"
      title="Headlines"
      className={className}
      state={state}
      minHeight={BLOCK_HEIGHT.column}
      skeleton={<SkeletonRows count={5} testId="headlines-skeleton" />}
      actions={
        <Link to="/news" className="shrink-0 text-[10px] text-accent-text hover:underline">
          All news
        </Link>
      }
    >
      {shown.length === 0 ? (
        <p className="px-2 py-2 text-[11px] text-fg-subtle">No headlines in this cycle.</p>
      ) : (
        <ul className="flex min-w-0 flex-col">
          {shown.map((item) => (
            <li
              key={`${item.headline}:${item.publishedAt ?? ""}`}
              className="flex h-6 min-w-0 items-baseline gap-2 border-b border-line/60 px-2 last:border-b-0"
            >
              {item.url === null ? (
                <span
                  title={item.headline}
                  className="min-w-0 flex-1 truncate text-[11px] text-fg"
                  data-news-text
                >
                  {item.headline}
                </span>
              ) : (
                <a
                  href={item.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={item.headline}
                  className="min-w-0 flex-1 truncate text-[11px] text-fg hover:text-accent-text"
                  data-news-link
                >
                  {item.headline}
                </a>
              )}
              <span className="shrink-0 text-[10px] text-fg-subtle">
                {shortStamp(item.publishedAt)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </HomeBlock>
  );
}
