/**
 * The per-ticker insider page: one company's Form 4 filings.
 *
 * The list at `/insider` is the whole wire; this page answers the narrower
 * question a reader asks when a name catches the eye — *which Form 4 filings
 * are behind this one company, and what does each say*. It is the same filing
 * read the panel draws, promoted to a URL (`/insider/{symbol}`) so a ticker's
 * insider history is linkable, bookmarkable and shareable like every other
 * page in the shell.
 *
 * Data comes from the one hook the panel already uses,
 * `GET /engine/insider/{symbol}`; the page adds the shell — a back link, the
 * page's own title, and a plain block that flows with the window instead of
 * the panel's sticky, self-scrolling box. A field the filing did not carry
 * stays absent here exactly as it does in the panel: this page invents no
 * number the SEC did not report.
 */

import { Link } from "@/shell/routes";

import {
  INSIDER_DETAIL_NOTE,
  INSIDER_DETAIL_TITLE_ID,
  InsiderDetail,
} from "./InsiderDetail";

export interface InsiderDetailPageProps {
  /** The company's ticker, uppercased by `parseRoute`. */
  readonly symbol: string;
}

export function InsiderDetailPage({ symbol }: InsiderDetailPageProps) {
  return (
    <main
      data-testid="insider-detail-page"
      className="min-h-0 flex-1 overflow-hidden bg-bg px-2 py-3 lg:px-4"
    >
      <div className="mx-auto flex w-full max-w-[1000px] flex-col gap-3">
        <nav aria-label="Breadcrumb" className="flex items-center gap-2 text-label">
          <Link
            to="/insider"
            data-testid="insider-detail-back"
            className="text-accent-text hover:underline"
          >
            ← All insider trading
          </Link>
        </nav>

        <header className="flex flex-col gap-1">
          <h1
            id={INSIDER_DETAIL_TITLE_ID}
            className="flex min-w-0 flex-wrap items-baseline gap-2 text-title font-medium text-fg"
          >
            <span className="font-mono">{symbol}</span>
            <span className="text-fg-muted">Form 4 filings</span>
          </h1>
          <p className="text-label text-fg-subtle">{INSIDER_DETAIL_NOTE}</p>
        </header>

        <InsiderDetail symbol={symbol} standalone hideHeader />
      </div>
    </main>
  );
}
