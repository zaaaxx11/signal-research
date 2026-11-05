/**
 * `/watchlist`: the symbols one reader follows, in their own order.
 *
 * The list is this browser's — there is no account in this product — so the page
 * opens with that sentence and then gets out of the way. Three things it will not
 * do:
 *
 * * **Ask the network for an empty list.** `useQuotes` has no address when there
 *   are no symbols, so the empty state costs nothing.
 * * **Ask more than once per set.** Every symbol goes into one
 *   `/market/quotes?symbols=…` call; adding or removing one refetches the set,
 *   and reordering refetches nothing because the order is not a market fact.
 * * **Invent a price.** A symbol the answer does not price keeps its row and says
 *   "Unavailable". Dropping it would look like the list forgot it; pricing it at
 *   zero would be a lie with a decimal point.
 *
 * Density follows the grid: 12 px type, 24 px rows, one hairline, no card per
 * row. The ticker links to its own page (a real `href`), the logo lane is
 * reserved whether or not the mark loads, and the two arrows and the × are
 * labelled buttons rather than a drag gesture — reordering must work from a
 * keyboard.
 */

import * as React from "react";

import { SectionTitle } from "@/components/SectionTitle";
import { ErrorState } from "@/components/ErrorState";
import { quoteProps } from "@/lib/quoteAttrs";
import { LogoMark } from "@/grid/LogoMark";
import { Link } from "@/shell/routes";

import { Delta, LastPrice, QuotesFailure, quoteCellState } from "./cells";
import { LocalNotice } from "./LocalNotice";
import { quoteFor } from "./quotes";
import { addSymbol, moveSymbol, removeSymbol, watchlistStore } from "./storage";
import { useLocalValue } from "./useLocalValue";
import { useQuotes } from "./useQuotes";

function ActionButton({
  label,
  disabled = false,
  onClick,
  children,
}: {
  readonly label: string;
  readonly disabled?: boolean;
  readonly onClick: () => void;
  readonly children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="focus-ring inline-flex h-5 w-5 items-center justify-center rounded-[2px] border border-line text-label leading-none text-fg-muted transition-colors hover:border-line-strong hover:text-fg disabled:cursor-not-allowed disabled:border-line/60 disabled:text-fg-disabled"
    >
      <span aria-hidden="true">{children}</span>
    </button>
  );
}

export function WatchlistPage() {
  const symbols = useLocalValue(watchlistStore);
  const quotes = useQuotes(symbols);
  const [draft, setDraft] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  const count = symbols.length;

  const add = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const result = addSymbol(symbols, draft);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    watchlistStore.write(result.symbols);
    setDraft("");
    setError(null);
  };

  const move = (symbol: string, delta: -1 | 1): void => {
    const next = moveSymbol(symbols, symbol, delta);
    // `null` means the row is already at the end it was sent to; nothing to write.
    if (next !== null) watchlistStore.write(next);
  };

  const remove = (symbol: string): void => {
    watchlistStore.write(removeSymbol(symbols, symbol));
  };

  return (
    <main data-testid="watchlist-page" className="min-h-0 flex-1 overflow-y-auto bg-bg px-2 py-2">
      <div className="flex min-w-0 items-baseline justify-between gap-3 px-0.5">
        <SectionTitle variant={2}>Watchlist</SectionTitle>
        <p
          data-testid="watchlist-count"
          className="font-mono text-label uppercase tracking-[0.08em] text-fg-subtle"
        >
          {count === 0 ? "Empty" : `${count} symbol${count === 1 ? "" : "s"}`}
        </p>
      </div>

      <LocalNotice />

      <form onSubmit={add} noValidate className="mt-2 flex flex-wrap items-end gap-1.5">
        <div className="min-w-0">
          <label
            htmlFor="watchlist-symbol"
            className="block text-label uppercase tracking-[0.06em] text-fg-subtle"
          >
            Symbol
          </label>
          <input
            id="watchlist-symbol"
            name="symbol"
            value={draft}
            onChange={(event) => {
              setDraft(event.target.value);
              setError(null);
            }}
            placeholder="AAPL, BTC-USD"
            autoComplete="off"
            spellCheck={false}
            aria-invalid={error !== null}
            className="focus-ring mt-0.5 h-7 w-52 rounded-sm border border-line-control bg-surface px-2 font-mono text-data uppercase text-fg placeholder:normal-case placeholder:text-fg-subtle"
          />
        </div>
        <button
          type="submit"
          className="focus-ring h-7 rounded-sm border border-line px-2.5 text-data text-fg-muted transition-colors hover:border-line-strong hover:text-fg"
        >
          Add
        </button>
      </form>

      {error !== null ? (
        <p role="alert" data-testid="watchlist-error" className="mt-1 text-data text-alert">
          {error}
        </p>
      ) : null}

      {count === 0 ? (
        <div
          data-testid="watchlist-empty"
          className="mt-2 rounded-md border border-line bg-surface px-3 py-3"
        >
          <p className="text-data text-fg">Your watchlist is empty.</p>
          <p className="mt-0.5 text-data text-fg-muted">
            Type a ticker in the field above — AAPL, MSFT or BTC-USD — and press Add. It stays in
            this browser.
          </p>
        </div>
      ) : (
        <>
          {quotes.status === "error" ? (
            <QuotesFailure error={quotes.error} onRetry={quotes.retry} />
          ) : null}
          {quotes.status === "loading" ? (
            <p role="status" className="mt-2 text-data text-fg-subtle">
              Loading quotes…
            </p>
          ) : null}

          <div className="mt-2 overflow-x-auto rounded-md border border-line bg-surface">
            <table
              data-testid="watchlist-table"
              className="w-full min-w-[560px] table-fixed border-collapse text-data"
            >
              <caption className="sr-only">
                Symbols you follow in this browser, in the order you put them
              </caption>
              <colgroup>
                <col className="w-[4%]" />
                <col className="w-[46%]" />
                <col className="w-[16%]" />
                <col className="w-[16%]" />
                <col className="w-[18%]" />
              </colgroup>
              <thead>
                <tr className="h-[26px] border-b border-line-strong text-label uppercase tracking-[0.05em] text-fg-subtle">
                  <th scope="col" className="px-2 text-left font-medium">
                    #
                  </th>
                  <th scope="col" className="px-2 text-left font-medium">
                    Symbol
                  </th>
                  <th scope="col" className="px-2 text-right font-medium">
                    Last
                  </th>
                  <th scope="col" className="px-2 text-right font-medium">
                    Change
                  </th>
                  <th scope="col" className="px-2 text-right font-medium">
                    Order
                  </th>
                </tr>
              </thead>
              <tbody>
                {symbols.map((symbol, index) => {
                  const quote = quoteFor(quotes.report, symbol);
                  const state = quoteCellState(quotes.status, quote);
                  return (
                    <tr
                      key={symbol}
                      data-testid={`watchlist-row-${symbol}`}
                      data-symbol={symbol}
                      data-missing={state === "missing" ? "true" : "false"}
                      className="h-6 border-b border-line/60 odd:bg-row-alt hover:bg-row-hover"
                      {...quoteProps({ symbol, name: quote?.name ?? undefined, price: quote?.price ?? null, changePct: quote?.changePct ?? null, kind: "stock" })}
                    >
                      <td className="nums px-2 text-fg-subtle">{index + 1}</td>
                      <td className="min-w-0 px-2">
                        <span className="flex min-w-0 items-center gap-1.5">
                          <LogoMark symbol={symbol} size={16} />
                          <Link
                            to={`/stock/${symbol}`}
                            className="font-mono text-data font-semibold text-fg hover:text-accent-text"
                          >
                            {symbol}
                          </Link>
                          {quote?.name ? (
                            <span className="truncate text-fg-subtle">{quote.name}</span>
                          ) : null}
                        </span>
                      </td>
                      <td data-testid={`watchlist-last-${symbol}`} className="px-2 text-right">
                        <LastPrice state={state} quote={quote} />
                      </td>
                      <td data-testid={`watchlist-change-${symbol}`} className="px-2 text-right">
                        <Delta state={state} value={quote?.changePct ?? null} />
                      </td>
                      <td className="px-2 text-right">
                        <span className="inline-flex items-center gap-0.5">
                          <ActionButton
                            label={`Move ${symbol} up`}
                            disabled={index === 0}
                            onClick={() => move(symbol, -1)}
                          >
                            ↑
                          </ActionButton>
                          <ActionButton
                            label={`Move ${symbol} down`}
                            disabled={index === count - 1}
                            onClick={() => move(symbol, 1)}
                          >
                            ↓
                          </ActionButton>
                          <ActionButton label={`Remove ${symbol}`} onClick={() => remove(symbol)}>
                            ×
                          </ActionButton>
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {count > 0 && quotes.status === "ready" && quotes.report !== null ? (
        <p className="mt-1 px-0.5 text-data text-fg-subtle">
          {quotes.report.missing.length === 0
            ? "Every symbol has a quote."
            : `${quotes.report.missing.length} symbol${
                quotes.report.missing.length === 1 ? "" : "s"
              } could not be priced: ${quotes.report.missing.join(", ")}.`}
        </p>
      ) : null}
    </main>
  );
}
