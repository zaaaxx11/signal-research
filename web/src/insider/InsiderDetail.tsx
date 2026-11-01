/**
 * The filings panel: the Form 4 rows behind one company.
 *
 * These are individual filings, each with its own filing and transaction
 * date. The panel says so in words, and it never adds them up: a sum across
 * filings would be a number nobody reported.
 *
 * Every fact comes from the filing's own payload. A field the filing did not
 * carry is absent, not `0` and not `$0.00`; a role it did not state reads
 * "Role not reported", never `undefined`. The `count` line reports the
 * endpoint's own count and says `shown of total` when the two disagree.
 */

import { formatCount } from "@/breadth/format";
import { cn } from "@/lib/utils";

import type { InsiderFiling } from "./filings";
import { useInsiderFilings } from "./useInsiderFilings";

export const INSIDER_DETAIL_TITLE_ID = "insider-detail-title";

/** Named in the panel and in its tests: the rows are filings, not totals. */
export const INSIDER_DETAIL_NOTE =
  "Individual filings as reported to the SEC, each with its own filing and transaction date.";

function Filing({ filing }: { readonly filing: InsiderFiling }) {
  return (
    <li
      data-testid="insider-filing"
      data-accession={filing.accession ?? undefined}
      className="border-b border-line/60 px-2 py-1.5 last:border-b-0"
    >
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
        <time
          dateTime={filing.filed ?? undefined}
          className="nums shrink-0 text-data text-fg-muted"
        >
          {filing.filed ?? "—"}
        </time>
        <span
          className="min-w-0 truncate text-data font-medium text-fg"
          title={filing.ownerName ?? "Owner not reported"}
        >
          {filing.ownerName ?? "Owner not reported"}
        </span>
        <span
          className="min-w-0 truncate text-data text-fg-subtle"
          title={filing.role ?? "Role not reported"}
        >
          {filing.role ?? "Role not reported"}
        </span>
      </div>

      {filing.accession === null ? null : filing.url === null ? (
        <span className="mt-0.5 block truncate font-mono text-label text-fg-subtle">
          {filing.accession}
        </span>
      ) : (
        <a
          href={filing.url}
          target="_blank"
          rel="noreferrer noopener"
          title={`Open Form 4 ${filing.accession} on SEC.gov`}
          className="focus-ring mt-0.5 block truncate font-mono text-label text-accent-text hover:underline"
        >
          Form 4 · {filing.accession}
        </a>
      )}

      {filing.fields.length === 0 ? null : (
        <dl className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5">
          {filing.fields.map((field) => (
            <div key={field.key} className="flex min-w-0 items-baseline justify-between gap-2">
              <dt className="shrink-0 text-label uppercase tracking-[0.05em] text-fg-subtle">
                {field.label}
              </dt>
              <dd
                data-field={field.key}
                title={field.text}
                className={cn(
                  "min-w-0 truncate text-data text-fg-muted",
                  field.key === "price" && "nums text-fg",
                  field.key === "code" && "font-mono",
                )}
              >
                {field.text}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  );
}

function Skeleton({ symbol }: { readonly symbol: string }) {
  return (
    <div data-testid="insider-detail-loading" role="status" aria-busy="true" className="px-2 py-2">
      <span className="sr-only">Loading Form 4 filings for {symbol}</span>
      {Array.from({ length: 5 }, (_, index) => (
        <div key={index} aria-hidden="true" className="mb-2 last:mb-0">
          <div className="flex items-center gap-2">
            <span className="block h-[9px] w-[64px] rounded-[1px] bg-surface-raised" />
            <span className="block h-[9px] w-[38%] rounded-[1px] bg-surface-raised" />
          </div>
          <div className="mt-1 flex items-center gap-2">
            <span className="block h-[9px] w-[28%] rounded-[1px] bg-surface-raised" />
            <span className="block h-[9px] w-[20%] rounded-[1px] bg-surface-raised" />
          </div>
        </div>
      ))}
    </div>
  );
}

export interface InsiderDetailProps {
  readonly symbol: string;
  readonly onClose: () => void;
}

export function InsiderDetail({ symbol, onClose }: InsiderDetailProps) {
  const view = useInsiderFilings(symbol);
  const filings = view.status === "ready" ? view.filings : null;
  const shown = filings?.items.length ?? 0;
  const total = filings?.count ?? null;

  return (
    <aside
      data-testid="insider-detail"
      aria-labelledby={INSIDER_DETAIL_TITLE_ID}
      className="flex min-h-0 min-w-0 flex-col self-start overflow-hidden rounded-md border border-line bg-surface xl:sticky xl:top-2 xl:max-h-[calc(100vh-120px)]"
    >
      <div className="flex items-start justify-between gap-2 border-b border-line-strong px-2 py-1.5">
        <div className="min-w-0">
          <h2
            id={INSIDER_DETAIL_TITLE_ID}
            className="flex min-w-0 items-baseline gap-1.5 text-ui font-medium text-fg"
          >
            <span className="font-mono">{symbol}</span>
            <span className="text-fg-muted">Form 4 filings</span>
          </h2>
          <p data-testid="insider-detail-note" className="mt-0.5 text-label text-fg-subtle">
            {INSIDER_DETAIL_NOTE}
          </p>
        </div>
        <button
          type="button"
          data-testid="insider-detail-close"
          onClick={onClose}
          className="focus-ring shrink-0 rounded-sm border border-line px-2 py-0.5 text-data text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
        >
          Close
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {view.status === "loading" ? (
          <Skeleton symbol={symbol} />
        ) : view.status === "error" ? (
          <div data-testid="insider-detail-error" role="alert" className="px-2 py-2">
            <p className="text-data text-fg">Form 4 filings for {symbol} did not load.</p>
            <p className="mt-0.5 text-data text-fg-muted">{view.error}</p>
            <button
              type="button"
              onClick={view.retry}
              className="focus-ring mt-1.5 rounded-sm border border-line px-2 py-0.5 text-data text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
            >
              Retry
            </button>
          </div>
        ) : filings === null || filings.items.length === 0 ? (
          <p data-testid="insider-detail-empty" className="px-2 py-2 text-data text-fg-muted">
            No Form 4 filings for {symbol}.
          </p>
        ) : (
          <>
            <p
              data-testid="insider-detail-count"
              className="nums border-b border-line/60 px-2 py-1 text-label uppercase tracking-[0.06em] text-fg-subtle"
            >
              {total !== null && total !== shown
                ? `${formatCount(shown)} of ${formatCount(total)} transactions`
                : `${formatCount(shown)} ${shown === 1 ? "transaction" : "transactions"}`}
              {filings.asOf === null ? null : ` · as of ${filings.asOf}`}
            </p>

            {filings.providerErrors.length === 0 ? null : (
              <p data-testid="insider-detail-errors" className="border-b border-line/60 px-2 py-1 text-label text-alert">
                Some filings could not be read: {filings.providerErrors.join("; ")}
              </p>
            )}

            <ul data-testid="insider-filings">
              {filings.items.map((filing) => (
                <Filing key={filing.key} filing={filing} />
              ))}
            </ul>
          </>
        )}
      </div>
    </aside>
  );
}
