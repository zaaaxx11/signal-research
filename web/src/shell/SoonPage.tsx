/**
 * `/soon/{slug}`: one page that names the section and says it is next.
 *
 * Every nav item has to go somewhere, and ten of the fourteen have no surface
 * yet. This is the somewhere: the section's own name, one sentence, and a way
 * back. It is rendered by the same shell — same nav, same clock — so the reader
 * never leaves the product to find out that a section is not built.
 *
 * The page is deliberately boring. A "coming soon" illustration, a fake table
 * or a disabled search box would all be more expensive and less honest than a
 * sentence.
 */

import { NAV_ITEMS } from "./nav";
import { Link } from "./routes";
import { type SoonSlug } from "./routes";

const COPY: Record<SoonSlug, string> = {
  news: "Headlines per symbol, once the news surface has a page of its own.",
};

export interface SoonPageProps {
  slug: SoonSlug;
}

export function SoonPage({ slug }: SoonPageProps) {
  const item = NAV_ITEMS.find((entry) => entry.path === `/soon/${slug}`);
  const label = item?.label ?? slug;

  return (
    <main data-testid="soon-page" data-slug={slug} className="flex min-h-0 flex-1 items-start justify-center overflow-y-auto px-4 py-16">
      <section className="w-full max-w-[520px] rounded-md border border-line bg-surface p-6">
        <p className="font-mono text-label uppercase tracking-[0.12em] text-fg-subtle">Next</p>
        <h1 className="mt-1 text-title font-semibold text-fg">{label}</h1>
        <p className="mt-2 text-data text-fg-muted">{COPY[slug]}</p>
        <p className="mt-3 text-data text-fg-subtle">
          This section is not built yet. The rest of the product is still live.
        </p>
        <div className="mt-5 flex items-center gap-3">
          <Link
            to="/"
            className="rounded-sm border border-line-control px-3 py-1 text-ui text-fg hover:bg-row-hover"
          >
            Back to signals
          </Link>
          <Link
            to="/screener"
            className="text-ui text-accent-text hover:text-fg"
          >
            Open the screener
          </Link>
        </div>
      </section>
    </main>
  );
}
