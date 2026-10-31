/**
 * News: headlines, a safe link, and the source's age in words.
 *
 * An `href` is a dangerous slot fed by a third-party RSS document. `safeHref`
 * admits absolute http(s) only; a `javascript:` URL is rendered as plain text,
 * so the headline stays readable and the link stays inert. Each anchor opens a
 * new tab with `rel="noopener noreferrer"`.
 */

import * as React from "react";

import { formatInstant, formatRelativeTime, safeHref } from "./format";
import { TabEmpty, TabError, TabSkeleton } from "./TabStates";

import type { NewsData, Resource } from "./useDetail";

export interface NewsTabProps {
  symbol: string;
  state: Resource<NewsData>;
  onRetry: () => void;
}

export function NewsTab({ symbol, state, onRetry }: NewsTabProps): React.ReactElement {
  if (state.status === "loading") {
    return <TabSkeleton label={`news for ${symbol}`} testId="news-loading" rows={10} />;
  }

  if (state.status === "error") {
    return <TabError label="news" testId="news-error" message={state.error} onRetry={onRetry} />;
  }

  if (state.status === "empty" || !state.data) {
    return <TabEmpty testId="news-empty">No recent news for {symbol}.</TabEmpty>;
  }

  return (
    <div data-testid="news-loaded">
      <ul className="flex flex-col">
        {state.data.items.map((item, index) => {
          const href = safeHref(item.url);
          return (
            <li
              key={`${item.headline}-${index}`}
              className="border-b border-line py-2 first:pt-0 last:border-b-0"
            >
              <h3 className="text-ui font-normal leading-snug">
                {href ? (
                  <a
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="break-words text-fg hover:text-brand focus-visible:text-brand"
                  >
                    {item.headline}
                  </a>
                ) : (
                  <span data-testid="news-plain" className="break-words text-fg">
                    {item.headline}
                  </span>
                )}
              </h3>
              <p className="mt-1 flex min-w-0 items-baseline gap-1.5 text-label tracking-[0.04em] text-fg-subtle">
                <span className="shrink-0">{item.source || "Unknown source"}</span>
                <span aria-hidden="true">·</span>
                {item.publishedAt ? (
                  <time
                    dateTime={item.publishedAt}
                    title={formatInstant(item.publishedAt)}
                    className="truncate"
                  >
                    {formatRelativeTime(item.publishedAt)}
                  </time>
                ) : (
                  <span>—</span>
                )}
              </p>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
