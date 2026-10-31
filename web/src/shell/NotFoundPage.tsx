/**
 * A URL this build does not answer.
 *
 * The server hands `/app/<anything>` the shell, so an unknown path is not a
 * 404 page from the server — it is this page, inside the nav, with two ways
 * out. That is the difference between a deep link that is wrong and a dead
 * end: the reader keeps the product around them.
 */

import { Link } from "./routes";

export interface NotFoundPageProps {
  path: string;
}

export function NotFoundPage({ path }: NotFoundPageProps) {
  return (
    <main
      data-testid="not-found"
      className="flex min-h-0 flex-1 items-start justify-center overflow-y-auto px-4 py-16"
    >
      <section className="w-full max-w-[520px] rounded-md border border-line bg-surface p-6">
        <p className="font-mono text-label uppercase tracking-[0.12em] text-fg-subtle">404</p>
        <h1 className="mt-1 text-title font-semibold text-fg">This page does not exist</h1>
        <p className="mt-2 break-all font-mono text-data text-fg-muted">{path}</p>
        <div className="mt-5 flex items-center gap-3">
          <Link
            to="/"
            className="rounded-sm border border-line-control px-3 py-1 text-ui text-fg hover:bg-row-hover"
          >
            Back to signals
          </Link>
          <Link to="/screener" className="text-ui text-accent-text hover:text-fg">
            Open the screener
          </Link>
        </div>
      </section>
    </main>
  );
}
