/**
 * The site footer: present on every route, not just the home board.
 *
 * It is one `<footer>` landmark in the app shell (`app.tsx`), a sibling of the
 * per-route `<main>`, so it sits at the bottom of the document on every page
 * and is skippable by name. The layout is a single grid: a wide brand column,
 * three link columns of real routes, and one quiet legal line. It reflows to
 * one column on a phone and carries no horizontal scroll.
 *
 * The rule the shell's other chrome holds here too: internal state is not
 * product copy. There is no version, no build stamp, no "as of" read and no
 * health line. A footer that says *what this is, where to go, who it is, and
 * one disclaimer* is the whole job.
 *
 * The upstreams are not product copy either: the footer names no data source
 * ("Powered by X, Y, Z" was removed at the operator's request). Where the
 * numbers come from is a kitchen secret — the reader sees the product, never
 * the supply chain.
 */

import { BrandMark } from "@/shell/BrandMark";
import { Link } from "@/shell/routes";
import { cn } from "@/lib/utils";

/** A footer link is a route: a real path the shell's server answers. */
interface FooterLink {
  readonly label: string;
  readonly to: string;
}

/** The three link columns, in the order a reader scans a footer. */
const COLUMNS: ReadonlyArray<{ title: string; links: readonly FooterLink[] }> = [
  {
    title: "Browse",
    links: [
      { label: "Stock screener", to: "/screener" },
      { label: "ETF screener", to: "/screener/etf" },
      { label: "Compare", to: "/compare" },
      { label: "Maps", to: "/map" },
      { label: "Charts", to: "/charts" },
    ],
  },
  {
    title: "Research",
    links: [
      { label: "News", to: "/news" },
      { label: "Groups", to: "/groups" },
      { label: "Insider", to: "/insider" },
      { label: "Calendar", to: "/calendar" },
      { label: "Futures", to: "/futures" },
      { label: "Forex", to: "/forex" },
    ],
  },
  {
    title: "Markets & you",
    links: [
      { label: "Crypto", to: "/crypto" },
      { label: "Watchlist", to: "/watchlist" },
      { label: "Portfolio", to: "/portfolio" },
    ],
  },
];

function FooterColumn({ title, links }: { title: string; links: readonly FooterLink[] }) {
  return (
    <nav aria-label={title} className="flex min-w-0 flex-col gap-1">
      <span className="mb-1 text-[10px] uppercase tracking-[0.08em] text-fg-subtle">{title}</span>
      <ul className="flex flex-col gap-1">
        {links.map((link) => (
          <li key={link.to} className="min-w-0">
            <Link to={link.to} className="block w-max max-w-full truncate text-[11px] text-fg-muted transition-colors hover:text-fg">
              {link.label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function SiteFooter() {
  return (
    <footer
      data-testid="site-footer"
      className={cn(
        "shrink-0 border-t border-line bg-surface px-4 py-6 text-fg-muted sm:px-6",
      )}
    >
      <div className="mx-auto grid w-full max-w-[1280px] gap-x-6 gap-y-5 sm:grid-cols-2 lg:grid-cols-[1.5fr_1fr_1fr_1fr]">
        <div className="flex flex-col gap-3 sm:col-span-2 lg:col-span-1">
          <div className="flex items-center gap-1.5 text-[13px] font-semibold text-fg">
            <BrandMark size={18} className="text-accent-text" />
            <span className="uppercase tracking-[0.16em]">Signal Research</span>
          </div>
          <p className="max-w-[42ch] text-[11px] leading-relaxed text-fg-muted">
            Free market research across US equities, ETFs, crypto, futures and
            forex. One dashboard, no account required.
          </p>
        </div>

        {COLUMNS.map((column) => (
          <FooterColumn key={column.title} title={column.title} links={column.links} />
        ))}
      </div>

      <div className="mx-auto mt-6 flex w-full max-w-[1280px] flex-col gap-1 border-t border-line pt-4 text-[10px] text-fg-subtle sm:flex-row sm:items-center sm:justify-between">
        <span>© 2026 Signal Research, a Customin product.</span>
        <span>Market data may be delayed. For informational purposes only, not investment advice.</span>
      </div>
    </footer>
  );
}
