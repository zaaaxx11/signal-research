/**
 * The top line: the day's headlines, sliding.
 *
 * A news channel has a ticker because a reader should be able to look up at any
 * moment and catch up without clicking anything. This is that line: one row,
 * full width, the newest stories moving right to left, each a real link out.
 *
 * How the loop works
 *   The track holds the stories twice and translates by exactly -50 %. When the
 *   first copy has left, the second sits where the first began, so the jump back
 *   to 0 is invisible. Duration is set from the number of stories rather than
 *   fixed, so a short list does not crawl and a long one does not sprint.
 *
 * Motion is a courtesy, not a requirement
 *   Hovering or focusing anywhere in the line pauses it (the reader is reading),
 *   and `@media (prefers-reduced-motion: reduce)` removes the animation
 *   entirely, leaving a normal horizontally scrollable row. The duplicate copy
 *   is `aria-hidden`, so a screen reader hears each story once.
 */

import type { BlockState, HomeNews } from "../home";
import { Link } from "@/shell/routes";
import { cn } from "@/lib/utils";

/** Stories in the ticker. A dozen is a line, not a feed. */
const TICKER_ITEMS = 12;

export interface HeadlineTickerProps {
  readonly state: BlockState;
  readonly news: readonly HomeNews[] | null;
  /** Layout from the page: the ticker is a bleed strip, not a card. */
  readonly className?: string;
}

function Item({ item }: { readonly item: HomeNews }) {
  const label = item.source.length > 0 ? `${item.headline} — ${item.source}` : item.headline;
  return (
    <li className="flex shrink-0 items-center gap-2">
      <span aria-hidden="true" className="text-[9px] text-accent-text">
        ●
      </span>
      {item.url === null ? (
        <span className="whitespace-nowrap text-[11px] text-fg-muted">{item.headline}</span>
      ) : (
        <a
          href={item.url}
          target="_blank"
          rel="noopener noreferrer"
          title={label}
          className="whitespace-nowrap text-[11px] text-fg-muted hover:text-accent-text"
        >
          {item.headline}
        </a>
      )}
    </li>
  );
}

export function HeadlineTicker({ state, news, className }: HeadlineTickerProps) {
  if (state.kind !== "ready" || news === null || news.length === 0) {
    // No ticker when there is nothing to tick: an empty bar that slides is a
    // broken promise. The error is named instead, in one quiet line.
    if (state.kind === "error" || state.kind === "missing") {
      return (
        <p data-testid="ticker-unavailable" className="px-2 py-1 text-[10px] text-fg-subtle">
          {state.reason ?? "Headlines unavailable."}
        </p>
      );
    }
    return null;
  }

  const stories = news.slice(0, TICKER_ITEMS);
  const seconds = Math.max(28, stories.length * 5);

  return (
    <section
      aria-label="Top headlines"
      data-testid="home-ticker"
      className={cn("group relative min-w-0 overflow-hidden", className)}
    >
      <div
        data-testid="ticker-track"
        className="zt-ticker flex w-max items-center gap-6 py-1 pl-2 will-change-transform"
        style={{ ["--zt-ticker-duration" as string]: `${seconds}s` }}
      >
        <ul className="flex shrink-0 items-center gap-6">
          {stories.map((item, index) => (
            <Item key={`a-${item.url ?? item.headline}-${index}`} item={item} />
          ))}
        </ul>
        {/* The second copy is what makes the loop seamless; it is decoration for
            the eye, so a screen reader is not read the news twice. */}
        <ul aria-hidden="true" className="flex shrink-0 items-center gap-6">
          {stories.map((item, index) => (
            <Item key={`b-${item.url ?? item.headline}-${index}`} item={item} />
          ))}
        </ul>
      </div>

      <Link
        to="/news"
        className="absolute inset-y-0 right-0 flex items-center bg-gradient-to-l from-bg via-bg px-2 text-[10px] uppercase tracking-[0.08em] text-fg-subtle hover:text-accent-text"
      >
        All news
      </Link>
    </section>
  );
}
